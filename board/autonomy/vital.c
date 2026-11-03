/* Native PS rPPG: configured Green/POS + frozen FIR + spectral peak, ported from
 * backend/rppg_color.py and backend/vital.py.
 *
 * Two time bases, matching the Python reference:
 *   - frame-id (offline playback): equal spacing by frame_id / fps;
 *   - source timestamps: linear re-grid at the frozen rate before FIR/POS, with the
 *     fs/jitter/gap guards. Interpolation only forms processing samples; the
 *     captured-sample count still gates the window.
 *
 * Arithmetic follows the Python reference exactly where it is observable:
 *   - ROI means are per-frame float RGB, never integers;
 *   - POS uses complete local windows, population std, and per-sample support;
 *   - Q1.15 uses half-away-from-zero on the equivalent mean 128*(1+pulse);
 *   - FIR accumulates in int64, shifts once (floor), saturates to int16;
 *   - the spectrum is evaluated only on the HR band bins, by direct DFT, which
 *     matches numpy's rfft bins at k*fs/N without a mixed-radix FFT.
 */
#include "vital.h"

#include <float.h>
#include <math.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CENTER 128
#define SCALE 256
#define PI 3.14159265358979323846

typedef struct {
    double rgb[3];
    uint64_t fid;
    double ts;
    int has_ts;
    int64_t green_q15;
} sample_t;

struct vg_vital {
    vg_vital_config cfg;
    sample_t *hist;
    size_t head, count, capacity;
    int need, analysis_capacity, color_window, warmup;
    int symmetric_fir;
    size_t scratch_capacity;
    double *accum;
    double *support;
    double *wave;
    double *chroma;
    double *orth;
    double *pulse;
    double *filtered;
    double *rgb;
    double *times;
    int64_t *q15;
    char blocked[32];
    int recovery_left;
    double asof_ts; /* latest observed time, including a quality pause */
    double min_rate_hz; /* fps * (1 - fs_tol): below this the FIR cannot be fed */
};

static double clamp_i16(double value)
{
    if (value > 32767.0) return 32767.0;
    if (value < -32768.0) return -32768.0;
    return value;
}

/* Python's `>>` on int64 floors toward -inf; C's >> on negatives is
 * implementation-defined, so divide explicitly. */
static int64_t floor_shift(int64_t value, int shift)
{
    int64_t divisor = (int64_t)1 << shift;
    if (value >= 0) return value >> shift;
    return -(((-value) + divisor - 1) >> shift);
}

static sample_t *slot(vg_vital *vital, size_t logical)
{
    return &vital->hist[(vital->head + logical) % vital->capacity];
}

static double stddev(const double *values, int count)
{
    double mean = 0.0, sum = 0.0;
    int i;
    for (i = 0; i < count; ++i) mean += values[i];
    mean /= (double)count;
    for (i = 0; i < count; ++i) {
        double d = values[i] - mean;
        sum += d * d;
    }
    return sqrt(sum / (double)count);
}

static int compare_double(const void *left, const void *right)
{
    double a = *(const double *)left, b = *(const double *)right;
    return a < b ? -1 : (a > b ? 1 : 0);
}

/* numpy's default percentile (linear interpolation) for p=95. */
static double percentile95(double *values, int count)
{
    double rank, fraction;
    int low, high;
    if (count <= 0) return 0.0;
    qsort(values, (size_t)count, sizeof(double), compare_double);
    if (count == 1) return values[0];
    rank = 0.95 * (double)(count - 1);
    low = (int)floor(rank);
    high = (int)ceil(rank);
    fraction = rank - (double)low;
    return values[low] + fraction * (values[high] - values[low]);
}

vg_vital *vg_vital_create(const vg_vital_config *config)
{
    vg_vital *vital;
    size_t scratch;
    if (!config || !config->coeffs || config->taps < 1 || config->shift < 0 ||
        !isfinite(config->fps) || config->fps <= 0 ||
        !isfinite(config->window_seconds) || config->window_seconds <= 0 ||
        !isfinite(config->color_window_seconds) || config->color_window_seconds <= 0 ||
        !isfinite(config->f_lo) || !isfinite(config->f_hi) ||
        !(0 < config->f_lo && config->f_lo < config->f_hi && config->f_hi < config->fps / 2) ||
        !isfinite(config->harmonic_ratio_min) || !(0 < config->harmonic_ratio_min && config->harmonic_ratio_min < 1) ||
        !isfinite(config->max_gap_s) || config->max_gap_s <= 0 ||
        !isfinite(config->jitter_ratio_max) || config->jitter_ratio_max <= 0 ||
        !isfinite(config->fs_tol) || config->fs_tol <= 0 || config->fs_tol >= 1 ||
        !isfinite(config->recovery_s) || config->recovery_s < 0 ||
        config->recovery_samples < 0 || (config->method!=0&&config->method!=1) ||
        !isfinite(config->history_seconds) || config->history_seconds<0 ||
        !isfinite(config->source_min_hz) || !(config->source_min_hz==0 ||
            (2*config->f_hi<config->source_min_hz && config->source_min_hz<=config->fps)) ||
        (config->green_center_before_fir!=0 && config->green_center_before_fir!=1) ||
        !isfinite(config->short_window_seconds) || !isfinite(config->short_window_min_conf) ||
        !((config->short_window_seconds==0 && config->short_window_min_conf==0) ||
          (config->short_window_seconds>0 && config->short_window_min_conf>=.5 && config->short_window_min_conf<1)))
        return NULL;
    vital = calloc(1, sizeof(*vital));
    if (!vital) return NULL;
    vital->cfg = *config;
    vital->symmetric_fir = 1;
    for (int k = 0; k < config->taps / 2; ++k)
        if (config->coeffs[k] != config->coeffs[config->taps - 1 - k])
            vital->symmetric_fir = 0;
    vital->asof_ts = NAN;
    vital->min_rate_hz = config->source_min_hz>0?config->source_min_hz:config->fps*(1.0-config->fs_tol);
    vital->need = (int)lround(config->window_seconds * config->fps);
    if (vital->need < 2) { free(vital); return NULL; }
    vital->analysis_capacity=(int)lround(fmax(config->window_seconds,config->history_seconds)*config->fps);
    vital->color_window = (int)ceil(config->color_window_seconds * config->fps);
    if (vital->color_window < 2 || (config->method==0&&vital->color_window > vital->analysis_capacity)) { free(vital); return NULL; }
    vital->warmup = (config->taps - 1) + (config->method==0?vital->color_window-1:0);
    vital->capacity = (size_t)vital->analysis_capacity + (size_t)vital->warmup + 1;
    /* The ring stores captured samples, but the re-grid runs at fps, so a feed
     * below the design rate expands to more grid points than captured samples.
     * Scratch arrays must cover that expansion; sizing them by the sample count
     * made every rate below fps fail with warmup_history_unavailable. */
    vital->scratch_capacity = (size_t)ceil((double)vital->capacity *
                                           (config->fps / vital->min_rate_hz)) + 2;
    vital->hist = calloc(vital->capacity, sizeof(*vital->hist));
    scratch = vital->scratch_capacity;
    vital->accum = calloc(scratch, sizeof(double));
    vital->support = calloc(scratch, sizeof(double));
    vital->wave = calloc(scratch, sizeof(double));
    vital->filtered = calloc(scratch, sizeof(double));
    vital->times = calloc(scratch, sizeof(double));
    vital->rgb = calloc(scratch * 3, sizeof(double));
    vital->q15 = calloc(scratch, sizeof(int64_t));
    vital->chroma = calloc((size_t)vital->color_window, sizeof(double));
    vital->orth = calloc((size_t)vital->color_window, sizeof(double));
    vital->pulse = calloc((size_t)vital->color_window, sizeof(double));
    if (!vital->hist || !vital->accum || !vital->support || !vital->wave ||
        !vital->filtered || !vital->times || !vital->rgb || !vital->q15 ||
        !vital->chroma || !vital->orth || !vital->pulse) {
        vg_vital_destroy(vital);
        return NULL;
    }
    return vital;
}

void vg_vital_destroy(vg_vital *vital)
{
    if (!vital) return;
    free(vital->hist);
    free(vital->accum);
    free(vital->support);
    free(vital->wave);
    free(vital->filtered);
    free(vital->times);
    free(vital->rgb);
    free(vital->q15);
    free(vital->chroma);
    free(vital->orth);
    free(vital->pulse);
    free(vital);
}

void vg_vital_reset(vg_vital *vital)
{
    if (!vital) return;
    vital->head = 0;
    vital->count = 0;
    vital->recovery_left = 0;
    vital->blocked[0] = '\0';
    vital->asof_ts = NAN;
    memset(vital->hist, 0, vital->capacity * sizeof(*vital->hist));
}

void vg_vital_invalidate(vg_vital *vital, const char *reason, double ts, int allow_recovery)
{
    double previous = NAN;
    int short_gap;
    if (!vital) return;
    /* The board reports the same cadence refusal on every analysed frame; an
     * empty window with that reason already recorded must not re-memset 48 KB. */
    if (vital->count == 0 && vital->recovery_left == 0 && vital->blocked[0] &&
        reason && !strcmp(vital->blocked, reason)) return;
    if (vital->count) previous = slot(vital, vital->count - 1)->ts;
    short_gap = allow_recovery && vital->cfg.recovery_s > 0 && vital->count > 0 &&
                isfinite(ts) && isfinite(previous) &&
                ts > previous && ts - previous <= vital->cfg.recovery_s;
    if (!short_gap) vg_vital_reset(vital);
    else vital->recovery_left = vital->cfg.recovery_samples;
    if(isfinite(ts)&&ts>0) vital->asof_ts=ts;
    snprintf(vital->blocked, sizeof(vital->blocked), "%s", reason ? reason : "invalid");
}

int vg_vital_samples(const vg_vital *vital)
{
    int n;
    if (!vital) return 0;
    if(vital->cfg.history_seconds>0) {
        n=vg_vital_history_samples(vital);
        return n<vital->need?n:vital->need;
    }
    return vital->count < (size_t)vital->need ? (int)vital->count : vital->need;
}

int vg_vital_history_samples(const vg_vital *vital)
{
    int n,limit;
    double seconds;
    if (!vital) return 0;
    limit=(int)(vital->count<(size_t)vital->analysis_capacity?vital->count:(size_t)vital->analysis_capacity);
    n=limit<vital->need?limit:vital->need;
    while(n<limit) {
        const sample_t *previous=&vital->hist[(vital->head+vital->count-(size_t)n-1)%vital->capacity];
        const sample_t *current=&vital->hist[(vital->head+vital->count-(size_t)n)%vital->capacity];
        if(previous->has_ts && current->has_ts) {
            if(!(current->ts>previous->ts)||current->ts-previous->ts>vital->cfg.max_gap_s)break;
        } else if(previous->has_ts||current->has_ts||current->fid!=previous->fid+1)break;
        ++n;
    }
    seconds=fmax(vital->cfg.window_seconds,vital->cfg.history_seconds);
    while(n>(vital->cfg.history_seconds>0?0:vital->need)) {
        const sample_t *first=&vital->hist[(vital->head+vital->count-(size_t)n)%vital->capacity];
        const sample_t *last=&vital->hist[(vital->head+vital->count-1)%vital->capacity];
        double end=vital->cfg.history_seconds>0&&isfinite(vital->asof_ts)?vital->asof_ts:last->ts;
        if(!first->has_ts||!last->has_ts||end-first->ts<=seconds)break;
        --n;
    }
    return n;
}

double vg_vital_required_hz(const vg_vital *vital)
{
    return vital ? vital->min_rate_hz : 0.0;
}

int vg_vital_push_rgb(vg_vital *vital, const uint32_t sums[3], uint32_t count,
                      uint64_t frame_id, double ts)
{
    sample_t *target;
    int channel;
    if (!vital || !sums || count == 0) return -1;
    for (channel = 0; channel < 3; ++channel)
        if ((uint64_t)sums[channel] > (uint64_t)255 * count) return -1;
    if (vital->blocked[0] && vital->recovery_left) {
        double previous = vital->count ? slot(vital, vital->count - 1)->ts : NAN;
        if (!isfinite(ts) || !isfinite(previous) || !(ts > previous) ||
            ts - previous > vital->cfg.recovery_s)
            vg_vital_reset(vital);
    }
    vital->blocked[0] = '\0';
    if (vital->recovery_left > 0) --vital->recovery_left;
    if (vital->count == vital->capacity) {
        vital->head = (vital->head + 1) % vital->capacity;
        --vital->count;
    }
    target = slot(vital, vital->count);
    for (channel = 0; channel < 3; ++channel)
        target->rgb[channel] = (double)sums[channel] / (double)count;
    {int64_t numerator=(int64_t)SCALE*sums[1]-(int64_t)CENTER*SCALE*count;
     target->green_q15=numerator>=0 ? (2*numerator+count)/(2*(int64_t)count) : -((-2*numerator+count)/(2*(int64_t)count));}
    target->fid = frame_id;
    target->ts = ts;
    target->has_ts = isfinite(ts) && ts > 0.0;
    vital->asof_ts=target->has_ts?ts:NAN;
    ++vital->count;
    return 0;
}

/* POS over `m` RGB triples: complete local windows, population std, support. */
static const char *pos_waveform(vg_vital *vital, const double *rgb, int m, double *wave)
{
    const int cw = vital->color_window;
    int s, i;
    if (m < cw) return "color_projection_invalid";
    memset(vital->accum, 0, (size_t)m * sizeof(double));
    memset(vital->support, 0, (size_t)m * sizeof(double));
    for (s = 0; s + cw <= m; ++s) {
        double mean[3] = {0, 0, 0}, ratio, pulse_mean = 0;
        for (i = 0; i < cw; ++i) {
            const double *px = rgb + 3 * (size_t)(s + i);
            mean[0] += px[0]; mean[1] += px[1]; mean[2] += px[2];
        }
        for (i = 0; i < 3; ++i) mean[i] /= (double)cw;
        if (mean[0] <= DBL_EPSILON) continue;
        for (i = 0; i < cw; ++i) {
            const double *px = rgb + 3 * (size_t)(s + i);
            /* Python divides where the channel mean is > eps and uses 0 otherwise. */
            double r = mean[0] > DBL_EPSILON ? px[0] / mean[0] : 0.0;
            double g = mean[1] > DBL_EPSILON ? px[1] / mean[1] : 0.0;
            double b = mean[2] > DBL_EPSILON ? px[2] / mean[2] : 0.0;
            vital->chroma[i] = g - b;
            vital->orth[i] = g + b - 2.0 * r;
        }
        if (stddev(vital->orth, cw) <= DBL_EPSILON) continue;
        ratio = stddev(vital->chroma, cw) / stddev(vital->orth, cw);
        for (i = 0; i < cw; ++i) vital->pulse[i] = vital->chroma[i] + ratio * vital->orth[i];
        for (i = 0; i < cw; ++i) pulse_mean += vital->pulse[i];
        pulse_mean /= (double)cw;
        for (i = 0; i < cw; ++i) {
            vital->accum[s + i] += vital->pulse[i] - pulse_mean;
            vital->support[s + i] += 1.0;
        }
    }
    for (i = 0; i < m; ++i) {
        if (vital->support[i] <= 0.0) return "color_projection_invalid";
        wave[i] = vital->accum[i] / vital->support[i];
    }
    return NULL;
}

static const char *quantise(vg_vital *vital, const double *wave, int m)
{
    int i;
    for (i = 0; i < m; ++i) {
        double equivalent = CENTER * (1.0 + wave[i]);
        double value, q;
        if (!isfinite(equivalent) || equivalent < 0.0 || equivalent > 255.0)
            return "color_signal_range";
        value = (equivalent - CENTER) * SCALE;
        q = value >= 0 ? floor(value + 0.5) : ceil(value - 0.5);
        vital->q15[i] = (int64_t)clamp_i16(q);
    }
    return NULL;
}

static const char *fir_window(vg_vital *vital, int64_t *samples, int m, double *out)
{
    const int taps = vital->cfg.taps;
    int i, k;
    if(vital->cfg.method==1&&vital->cfg.green_center_before_fir) {
        int64_t sum=0,offset;
        for(i=0;i<m;++i)sum+=samples[i];
        double mean=(double)sum/m;
        offset=(int64_t)(mean>=0?floor(mean+.5):ceil(mean-.5));
        for(i=0;i<m;++i) {
            samples[i]-=offset;
            if(samples[i]<-32768||samples[i]>32767)return "green_signal_range";
        }
    }
    for (i = 0; i < m; ++i) {
        int64_t acc = 0;
        double value;
        if (vital->symmetric_fir && i >= taps - 1) {
            /* Pair equal coefficients only after the full history is present.
             * Accumulate before the original shift: integer results, startup
             * transients and saturation rejection remain unchanged. */
            for (k = 0; k < taps / 2; ++k)
                acc += (int64_t)vital->cfg.coeffs[k] *
                       (samples[i - k] + samples[i - (taps - 1 - k)]);
            if (taps % 2)
                acc += (int64_t)vital->cfg.coeffs[taps / 2] * samples[i - taps / 2];
        } else {
            for (k = 0; k < taps && k <= i; ++k)
                acc += (int64_t)vital->cfg.coeffs[k] * samples[i - k];
        }
        value = (double)floor_shift(acc, vital->cfg.shift);
        if (value > 32767.0 || value < -32768.0) return "color_fir_saturation";
        out[i] = value;
    }
    return NULL;
}

/* Linear re-grid at 1/fps, mirroring the selected Python estimator. */
static const char *uniform_rewindow(vg_vital *vital, int m, int n, int *count_out,int *start_out)
{
    int first = m - n, i, j, channel, full_count, count, index = 0;
    double end, start, *time = vital->times;
    double cutoff=slot(vital,(size_t)(m-n))->ts-(double)vital->warmup/vital->cfg.fps;
    while (first > 0) {
        const sample_t *previous = slot(vital, (size_t)(first - 1));
        const sample_t *current = slot(vital, (size_t)first);
        if(current->has_ts && current->ts<=cutoff)break;
        if (!previous->has_ts || !current->has_ts ||
            !(previous->ts > 0.0) || !(current->ts > 0.0) ||
            previous->ts >= current->ts || current->ts - previous->ts > vital->cfg.max_gap_s)
            break;
        --first;
    }
    for (i = first; i < m; ++i)
        if (!slot(vital, (size_t)i)->has_ts) return "invalid_timestamps";
    end = slot(vital, (size_t)(m - 1))->ts;
    start = slot(vital, (size_t)(m - n))->ts;
    full_count = (int)floor((end - slot(vital, (size_t)first)->ts) * vital->cfg.fps) + 1;
    count = (int)floor((end - start) * vital->cfg.fps) + 1;
    if(vital->cfg.method==0&&full_count<vital->color_window)return "color_window_not_full";
    if (count < 2 || count>full_count || full_count > (int)vital->scratch_capacity)
        return "warmup_history_unavailable";
    for (j = 0; j < full_count; ++j) time[j] = end - (double)(full_count - 1 - j) / vital->cfg.fps;
    for (j = 0; j < full_count; ++j) {
        const double target = time[j];
        while (index < m - first - 2 && slot(vital, (size_t)(first + index + 1))->ts < target)
            ++index;
        {
            const sample_t *left = slot(vital, (size_t)(first + index));
            const sample_t *right = slot(vital, (size_t)(first + index + 1));
            double span = right->ts - left->ts;
            double weight = span > 0 ? (target - left->ts) / span : 0.0;
            for (channel = 0; channel < 3; ++channel)
                vital->rgb[3 * (size_t)j + (size_t)channel] =
                    left->rgb[channel] + weight * (right->rgb[channel] - left->rgb[channel]);
            if(vital->cfg.method==1) {
                double q=(double)left->green_q15+weight*(double)(right->green_q15-left->green_q15);
                vital->q15[j]=(int64_t)(q>=0?floor(q+.5):ceil(q-.5));
            }
        }
    }
    if(vital->cfg.method==0) {
        const char *reject = pos_waveform(vital, vital->rgb, full_count, vital->wave);
        if (reject) return reject;
    }
    if(vital->cfg.method==0) {
        const char *reject = quantise(vital, vital->wave, full_count);
        if (reject) return reject;
    }
    {
        const char *reject = fir_window(vital, vital->q15, full_count, vital->filtered);
        if (reject) return reject;
    }
    *count_out = count;
    *start_out=full_count-count;
    return NULL;
}

/* Power at one exact rfft bin, evaluated by a direct sum over the window. */
static double bin_power(const double *values, int n, int k)
{
    double re = 0.0, im = 0.0, step = -2.0 * PI * (double)k / (double)n;
    double cs=cos(step),sn=sin(step),c=1.0,s=0.0;
    int i;
    for (i = 0; i < n; ++i) {
        double next;
        re+=values[i]*c;im+=values[i]*s;
        next=c*cs-s*sn;s=s*cs+c*sn;c=next;
    }
    return re * re + im * im;
}

static int bin_index(int i, int lo, int hi) { return i < lo ? lo : (i > hi ? hi : i); }

int vg_vital_estimate(vg_vital *vital, vg_vital_result *out)
{
    int n, m, i, k, k_lo, k_hi, best, bins, count, window_count, window_start, source_ts;
    double mean = 0.0, span, fs_eff, total = 0.0, conf, best_freq;
    double *y = NULL, *powers = NULL;
    const char *reject;
    if (!vital || !out) return -1;
    memset(out, 0, sizeof(*out));
    n = vg_vital_history_samples(vital);
    m = (int)(vital->count < (size_t)vital->capacity ? vital->count : vital->capacity);
    if (vital->blocked[0]) { out->reject = vital->blocked; return 0; }
    if (vital->recovery_left) { out->reject = "recovering"; return 0; }
    if (n < vital->need) { out->reject = "window_not_full"; return 0; }

    source_ts = 0;
    for (i = m - n; i < m; ++i) if (slot(vital, (size_t)i)->has_ts) source_ts = 1;
    if (source_ts) {
        double *diffs = vital->times; /* scratch: window diffs */
        double p95;
        int gap_exceeded=0;
        for (i = m - n; i < m; ++i)
            if (!slot(vital, (size_t)i)->has_ts) { out->reject = "invalid_timestamps"; return 0; }
        for (i = m - n + 1; i < m; ++i)
            if (!(slot(vital, (size_t)i)->ts > slot(vital, (size_t)(i - 1))->ts))
                { out->reject = "invalid_timestamps"; return 0; }
        span = slot(vital, (size_t)(m - 1))->ts - slot(vital, (size_t)(m - n))->ts;
        if (!(span > 0)) { out->reject = "no_time_span"; return 0; }
        fs_eff = (double)(n - 1) / span;
        for (i = 0; i < n - 1; ++i) {
            diffs[i] = slot(vital, (size_t)(m - n + 1 + i))->ts - slot(vital, (size_t)(m - n + i))->ts;
            if(diffs[i]>vital->cfg.max_gap_s)gap_exceeded=1;
        }
        p95=percentile95(diffs,n-1);
        if(gap_exceeded&&p95<=vital->cfg.max_gap_s){out->reject="sampling_gap";return 0;}
        /* Name the real cause before the generic tolerance check: a caller that
         * feeds the estimator from a slow analysis loop must be told the rate
         * is unusable, not that it missed the design rate by some fraction. */
        if (fs_eff < vital->min_rate_hz) { out->reject = "sampling_too_slow"; return 0; }
        /* Timestamp re-gridding fixes the FIR/frequency grid at cfg.fps. In
         * explicit density mode the source may be slower; do not relabel it as
         * nominal-rate acquisition. Legacy callers keep both tolerance guards. */
        if ((vital->cfg.source_min_hz==0 && fabs(fs_eff - vital->cfg.fps) > vital->cfg.fs_tol * vital->cfg.fps) ||
            fs_eff>vital->cfg.fps*(1.0+vital->cfg.fs_tol))
            { out->reject = "fs_mismatch"; return 0; }
        if (p95 * vital->cfg.fps > vital->cfg.jitter_ratio_max)
            { out->reject = "sampling_jitter"; return 0; }
        if(gap_exceeded){out->reject="sampling_gap";return 0;}
    } else {
        const sample_t *first = slot(vital, (size_t)(m - n));
        const sample_t *last = slot(vital, (size_t)(m - 1));
        span = (double)(last->fid - first->fid) / vital->cfg.fps;
        if (!(span > 0)) { out->reject = "no_time_span"; return 0; }
        fs_eff = (double)(n - 1) / span;
        if (!(fs_eff > 0 && fs_eff <= vital->cfg.fps * 1.001)) {
            out->reject = "fs_eff_implausible";
            return 0;
        }
    }

    if (source_ts) {
        reject = uniform_rewindow(vital, m, n, &window_count,&window_start);
        if (reject) { out->reject = reject; return 0; }
        /* Re-gridded length differs from the captured-sample count whenever
         * actual cadence differs from fps. Select the tail of the DERIVED
         * array, not m-count (which can become a negative offset). */
        y = vital->filtered + window_start;
        fs_eff = vital->cfg.fps;
    } else {
        for (i = m - n + 1; i < m; ++i)
            if (slot(vital, (size_t)i)->fid != slot(vital, (size_t)(i - 1))->fid + 1)
                { out->reject = "nonuniform_frame_ids"; return 0; }
        for (i = 0; i < m; ++i) {
            const sample_t *item = slot(vital, (size_t)i);
            vital->rgb[3 * (size_t)i] = item->rgb[0];
            vital->rgb[3 * (size_t)i + 1] = item->rgb[1];
            vital->rgb[3 * (size_t)i + 2] = item->rgb[2];
        }
        if(vital->cfg.method==0) {
            reject = pos_waveform(vital, vital->rgb, m, vital->wave);
            if (reject) { out->reject = reject; return 0; }
            reject = quantise(vital, vital->wave, m);
            if (reject) { out->reject = reject; return 0; }
        } else for(i=0;i<m;++i)vital->q15[i]=slot(vital,(size_t)i)->green_q15;
        reject = fir_window(vital, vital->q15, m, vital->filtered);
        if (reject) { out->reject = reject; return 0; }
        y = vital->filtered + (m - n);
        window_count = n;
    }
    count = window_count;

    /* Detrend + Hann over the window that will be transformed. */
    for (i = 0; i < count; ++i) {
        mean += y[i];
        vital->wave[i] = y[i];
    }
    mean /= (double)count;
    for (i = 0; i < count; ++i) {
        double hann = 0.5 - 0.5 * cos(2.0 * PI * (double)i / (double)(count - 1));
        vital->wave[i] = (vital->wave[i] - mean) * hann;
    }

    k_lo = (int)ceil(vital->cfg.f_lo * (double)count / fs_eff - 1e-12);
    k_hi = (int)floor(vital->cfg.f_hi * (double)count / fs_eff + 1e-12);
    if (k_lo < 1) k_lo = 1;
    if (k_hi > count / 2) k_hi = count / 2;
    bins = k_hi - k_lo + 1;
    if (bins < 3) { out->reject = "band_too_narrow"; return 0; }
    powers = malloc((size_t)bins * sizeof(double));
    if (!powers) { out->reject = "allocation_failed"; return 0; }
    for (k = 0; k < bins; ++k) {
        powers[k] = bin_power(vital->wave, count, k_lo + k);
        total += powers[k];
    }
    if (!(total > 0)) { free(powers); out->reject = "no_band_energy"; return 0; }

    best = 0;
    for (k = 1; k < bins; ++k) if (powers[k] > powers[best]) best = k;
    {
        int lo = bin_index(best - 1, 0, bins - 1), hi = bin_index(best + 1, 0, bins - 1);
        double local = 0.0;
        for (k = lo; k <= hi; ++k) local += powers[k];
        conf = local / total;
    }
    if (conf <= 0.5) { free(powers); out->reject = "no_dominant_peak"; return 0; }
    best_freq = (double)(k_lo + best) * fs_eff / (double)count;
    {
        double half = best_freq / 2.0;
        if (half >= vital->cfg.f_lo && half <= vital->cfg.f_hi) {
            int j = 0, chosen = 0, c;
            int candidates[3];
            double best_diff = fabs((double)k_lo * fs_eff / (double)count - half);
            double half_energy = 0.0, dominant = 0.0;
            for (k = 1; k < bins; ++k) {
                double diff = fabs((double)(k_lo + k) * fs_eff / (double)count - half);
                if (diff < best_diff) { best_diff = diff; j = k; }
            }
            candidates[0] = bin_index(j - 1, 0, bins - 1);
            candidates[1] = j;
            candidates[2] = bin_index(j + 1, 0, bins - 1);
            for (c = 0; c < 3; ++c) if (powers[candidates[c]] > powers[candidates[chosen]]) chosen = c;
            j = candidates[chosen];
            {
                int local_peak = (j == 0 || powers[j] > powers[j - 1]) &&
                                 (j + 1 == bins || powers[j] > powers[j + 1]);
                int lo = bin_index(j - 1, 0, bins - 1), hi = bin_index(j + 1, 0, bins - 1);
                if (local_peak) for (k = lo; k <= hi; ++k) half_energy += powers[k];
                lo = bin_index(best - 1, 0, bins - 1);
                hi = bin_index(best + 1, 0, bins - 1);
                for (k = lo; k <= hi; ++k) dominant += powers[k];
                if (local_peak && dominant > 0 && half_energy / dominant >= vital->cfg.harmonic_ratio_min) {
                    free(powers);
                    out->reject = "harmonic_ambiguity";
                    return 0;
                }
            }
        }
    }
    if(vital->cfg.short_window_seconds>0 && (double)count/fs_eff<=vital->cfg.short_window_seconds &&
       conf<=vital->cfg.short_window_min_conf) {
        free(powers);out->reject="short_window_low_confidence";return 0;
    }
    free(powers);
    out->present = 1;
    out->hr_bpm = round(best_freq * 600.0) / 10.0;
    out->hr_conf = round(conf * 10000.0) / 10000.0;
    return 0;
}
