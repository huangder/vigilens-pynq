/* Native contract JSON writer (docs/interface.md §1, nine top-level fields).
 *
 * Why this exists: the board must publish the contract itself, not ship numbers to
 * the PC for formatting. The writer only serializes; every value is produced by
 * vg_measure/vg_decision and the caller's same-frame face record. It replaces
 * nothing in the Python path and adds no field.
 *
 * Number formatting is %.17g so a double round-trips; JSON parsers (backend/
 * contract.py included) accept it. The embedding executable must keep
 * LC_NUMERIC=C, otherwise the decimal separator would break the document. */
#include "contract_json.h"

#include <math.h>
#include <stdarg.h>
#include <string.h>

typedef struct {
    char *buffer;
    size_t capacity;
    size_t used;
    int failed;
} writer;

static void put(writer *w, const char *format, ...)
{
    va_list args;
    int length;
    if (w->failed) return;
    va_start(args, format);
    length = vsnprintf(w->buffer + w->used, w->capacity - w->used, format, args);
    va_end(args);
    if (length < 0 || (size_t)length >= w->capacity - w->used) {
        w->failed = 1;
        return;
    }
    w->used += (size_t)length;
}

static void put_json_string(writer *w, const char *text)
{
    const unsigned char *p = (const unsigned char *)(text ? text : "");
    put(w, "\"");
    for (; *p; ++p) {
        if (*p == '"' || *p == '\\') put(w, "\\%c", *p);
        else if (*p < 0x20) put(w, "\\u%04x", (unsigned)*p);
        else put(w, "%c", *p);
    }
    put(w, "\"");
}

static void put_number_or_null(writer *w, double value, int present)
{
    if (present && isfinite(value)) put(w, "%.17g", value);
    else put(w, "null");
}

int vg_contract_line(char *buffer, size_t capacity,
                     const vg_measure_result *result,
                     const vg_contract_face *face,
                     const vg_vitals *vital)
{
    writer w;
    const vg_behavior_result *behavior;
    const vg_quality_result *quality;
    const vg_decision_result *decision;
    int ok;
    if (!buffer || capacity < 2 || !result || !face) return -1;
    if (!isfinite(result->ts) || !isfinite(face->yaw) || !isfinite(face->pitch) ||
        !isfinite(face->roll) || !isfinite(face->visible))
        return -1;
    if (face->visible < 0.0 || face->visible > 1.0) return -1;
    behavior = &result->behavior;
    quality = &result->quality;
    decision = &result->decision;
    w.buffer = buffer;
    w.capacity = capacity;
    w.used = 0;
    w.failed = 0;

    put(&w, "{\"ts\":%.17g,\"frame_id\":%llu", result->ts,
        (unsigned long long)result->frame_id);
    put(&w, ",\"face\":{\"visible\":%.17g,\"bbox\":[%d,%d,%d,%d],\"pose\":{\"yaw\":%.17g,\"pitch\":%.17g,\"roll\":%.17g}}",
        face->visible, face->bbox[0], face->bbox[1], face->bbox[2], face->bbox[3],
        face->yaw, face->pitch, face->roll);
    put(&w, ",\"behavior\":{\"ear_left\":%.17g,\"ear_right\":%.17g,\"blink_state\":",
        behavior->ear_left, behavior->ear_right);
    put_json_string(&w, vg_eye_state_name(behavior->blink_state));
    put(&w, ",\"blink_count\":%llu,\"blink_rate_per_min\":%.17g,\"perclos\":%.17g,"
           "\"long_close_count\":%llu,\"mar\":%.17g,\"yawn_count\":%llu}",
        (unsigned long long)behavior->blink_count, behavior->blink_rate_per_min,
        behavior->perclos, (unsigned long long)behavior->long_close_count,
        behavior->mar, (unsigned long long)behavior->yawn_count);
    put(&w, ",\"vital\":{\"hr_bpm\":");
    put_number_or_null(&w, vital ? vital->value[0] : 0.0, vital ? vital->present[0] : 0);
    put(&w, ",\"hr_conf\":");
    put_number_or_null(&w, vital ? vital->value[1] : 0.0, vital ? vital->present[1] : 0);
    put(&w, ",\"rr_per_min\":");
    put_number_or_null(&w, vital ? vital->value[2] : 0.0, vital ? vital->present[2] : 0);
    put(&w, ",\"rr_conf\":");
    put_number_or_null(&w, vital ? vital->value[3] : 0.0, vital ? vital->present[3] : 0);
    put(&w, "},\"quality\":{\"light_score\":%.17g,\"motion_score\":%.17g,\"overall\":%.17g}",
        quality->light_score, quality->motion_score, quality->overall);
    put(&w, ",\"status\":");
    put_json_string(&w, decision->status);
    put(&w, ",\"advice\":");
    put_json_string(&w, decision->advice);
    put(&w, ",\"reason\":");
    put_json_string(&w, decision->reason);
    put(&w, "}");
    if (w.failed) { buffer[0] = '\0'; return -1; }
    ok = (w.used < w.capacity);
    return ok ? 0 : -1;
}

int vg_contract_write(FILE *stream, const vg_measure_result *result,
                      const vg_contract_face *face, const vg_vitals *vital)
{
    char buffer[4096];
    if (!stream) return -1;
    if (vg_contract_line(buffer, sizeof(buffer), result, face, vital)) return -1;
    if (fputs(buffer, stream) == EOF) return -1;
    if (fputc('\n', stream) == EOF) return -1;
    return 0;
}
