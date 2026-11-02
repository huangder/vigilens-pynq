#include "decision.h"
#include <inttypes.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct vg_decision {
    vg_decision_config cfg;
    uint64_t out_streak, in_streak;
    int flagged, last_pitch;
    double last_value, last_limit;
};
static int valid_face(const vg_decision_face *f) {
    return f && isfinite(f->visible) && isfinite(f->yaw) && isfinite(f->pitch);
}
vg_decision *vg_decision_create(const vg_decision_config *c) {
    vg_decision *e;
    if (!c || !isfinite(c->quality_min_score) || !isfinite(c->face_visible_min) ||
        !isfinite(c->pose_yaw_max_deg) || !isfinite(c->pose_pitch_max_deg) ||
        !isfinite(c->perclos_warning) || !isfinite(c->fatigue_blink_rate_low) ||
        !isfinite(c->vital_require_quality)) return NULL;
    e=calloc(1, sizeof(*e));
    if (e) { e->cfg=*c; if (!e->cfg.pose_hold_frames) e->cfg.pose_hold_frames=1; }
    return e;
}
void vg_decision_destroy(vg_decision *e) { free(e); }
static void trigger(vg_decision_result *r, const char *rule, const char *metric,
                    double value, double threshold, const char *verdict) {
    vg_decision_trigger *t=&r->triggers[r->trigger_count++];
    t->rule=rule; t->metric=metric; t->value=value; t->threshold=threshold; t->verdict=verdict;
}
static void words(vg_decision_result *r, const char *status, const char *advice) {
    snprintf(r->status, sizeof(r->status), "%s", status);
    snprintf(r->advice, sizeof(r->advice), "%s", advice);
}
static void fired(vg_decision_result *r, const char *text) {
    size_t length=strlen(r->reason);
    snprintf(r->reason+length, sizeof(r->reason)-length, "%s%s", length ? "；" : "触发：", text);
}
int vg_decision_decide(vg_decision *e, const vg_decision_input *i, vg_decision_result *out) {
    vg_decision_result r={0};
    const vg_decision_config *c;
    int pose_out;
    char text[1024];
    if (!e || !i || !out || !valid_face(&i->face) || !isfinite(i->overall) ||
        !isfinite(i->light_score) || !isfinite(i->motion_score) ||
        !isfinite(i->perclos) || !isfinite(i->blink_rate_per_min)) return -1;
    c=&e->cfg;
    if (i->overall<c->quality_min_score) {
        const char *weak="光照";
        double score=i->light_score, value=score;
        /* Strict comparisons preserve Python min()'s first-item tie rule. */
        if (1.0-i->motion_score<score) { score=1.0-i->motion_score; value=i->motion_score; weak="运动"; }
        if (i->face.visible<score) { value=i->face.visible; weak="人脸可见率"; }
        trigger(&r,"quality_gate","quality.overall",i->overall,c->quality_min_score,"fail");
        words(&r,"unreliable","信号不可靠，暂不输出测量结果，请改善光照或保持稳定");
        snprintf(r.reason,sizeof(r.reason),"信号质量 %.2f 低于门控阈值 %.2f（最弱项：%s %.2f）",
                 i->overall,c->quality_min_score,weak,value);
        *out=r; return 0;
    }
    trigger(&r,"quality_gate","quality.overall",i->overall,c->quality_min_score,"pass");
    if (i->face.visible<c->face_visible_min) {
        trigger(&r,"face_visible","face.visible",i->face.visible,c->face_visible_min,"fail");
        words(&r,"adjust_posture","请正对摄像头，保持面部完整出现在画面内");
        snprintf(r.reason,sizeof(r.reason),"人脸可见率 %.2f 低于下限 %.2f",i->face.visible,c->face_visible_min);
        *out=r; return 0;
    }
    pose_out=fabs(i->face.yaw)>c->pose_yaw_max_deg || fabs(i->face.pitch)>c->pose_pitch_max_deg;
    if (pose_out) {
        if (e->out_streak<UINT64_MAX) ++e->out_streak;
        e->in_streak=0;
        e->last_pitch=!(fabs(i->face.yaw)>c->pose_yaw_max_deg);
        e->last_value=e->last_pitch ? i->face.pitch : i->face.yaw;
        e->last_limit=e->last_pitch ? c->pose_pitch_max_deg : c->pose_yaw_max_deg;
    } else {
        e->out_streak=0;
        if (e->in_streak<UINT64_MAX) ++e->in_streak;
    }
    if (pose_out && e->out_streak>=c->pose_hold_frames) e->flagged=1;
    else if (!pose_out && e->in_streak>=c->pose_hold_frames) e->flagged=0;
    if (e->flagged) {
        const char *axis=e->last_pitch ? "pitch" : "yaw";
        trigger(&r,"pose_range",e->last_pitch ? "face.pose.pitch" : "face.pose.yaw",e->last_value,e->last_limit,"fail");
        words(&r,"adjust_posture","头部偏离过大，请调整姿势正对摄像头");
        if (pose_out) snprintf(text,sizeof(text),"连续 %" PRIu64 " 帧越界",e->out_streak);
        else snprintf(text,sizeof(text),"已连续 %" PRIu64 "/%" PRIu32 " 帧回到范围内",e->in_streak,c->pose_hold_frames);
        snprintf(r.reason,sizeof(r.reason),"头部 %s = %.1f° 超出 ±%.0f°（%s）",axis,e->last_value,e->last_limit,text);
        *out=r; return 0;
    }
    if (i->perclos>c->perclos_warning) {
        snprintf(text,sizeof(text),"PERCLOS %.2f > %.2f",i->perclos,c->perclos_warning); fired(&r,text);
        trigger(&r,"perclos","behavior.perclos",i->perclos,c->perclos_warning,"warn");
    }
    if (i->long_close_count>=c->fatigue_long_close_count) {
        snprintf(text,sizeof(text),"长闭眼 %" PRIu64 " 次 ≥ %" PRIu64,i->long_close_count,c->fatigue_long_close_count); fired(&r,text);
        trigger(&r,"long_close","behavior.long_close_count",(double)i->long_close_count,(double)c->fatigue_long_close_count,"warn");
    }
    if (i->yawn_count>=c->fatigue_yawn_count) {
        snprintf(text,sizeof(text),"打哈欠 %" PRIu64 " 次 ≥ %" PRIu64,i->yawn_count,c->fatigue_yawn_count); fired(&r,text);
        trigger(&r,"yawn","behavior.yawn_count",(double)i->yawn_count,(double)c->fatigue_yawn_count,"warn");
    }
    if (i->blink_rate_per_min>0.0 && i->blink_rate_per_min<c->fatigue_blink_rate_low) {
        snprintf(text,sizeof(text),"眨眼率 %.1f/min < %.0f",i->blink_rate_per_min,c->fatigue_blink_rate_low); fired(&r,text);
        trigger(&r,"blink_rate_low","behavior.blink_rate_per_min",i->blink_rate_per_min,c->fatigue_blink_rate_low,"warn");
    }
    if (r.reason[0]) words(&r,"fatigue_risk","疲劳风险升高，建议休息 5 分钟或远眺放松");
    else {
        words(&r,"normal","状态正常");
        snprintf(r.reason,sizeof(r.reason),"各项指标均在正常范围（PERCLOS %.2f、眨眼率 %.1f/min），信号质量良好",i->perclos,i->blink_rate_per_min);
    }
    *out=r; return 0;
}
int vg_decision_vitals_allowed(const vg_decision *e, double q, const vg_decision_face *f, const char *s) {
    if (!e || !isfinite(q) || !valid_face(f)) return 0;
    return q>=fmax(e->cfg.quality_min_score,e->cfg.vital_require_quality) &&
           f->visible>=e->cfg.face_visible_min && fabs(f->yaw)<=e->cfg.pose_yaw_max_deg &&
           fabs(f->pitch)<=e->cfg.pose_pitch_max_deg &&
           (!s || (strcmp(s,"unreliable") && strcmp(s,"adjust_posture") && strcmp(s,"disconnected")));
}
void vg_decision_gate_vitals(const vg_decision *e, double q, const vg_vitals *in,
                            const vg_decision_face *face, const char *status, vg_vitals *out) {
    vg_vitals result={0};
    if (!out) return;
    if (e && in && isfinite(q) && q>=fmax(e->cfg.quality_min_score,e->cfg.vital_require_quality) &&
        (!face || vg_decision_vitals_allowed(e,q,face,status))) result=*in;
    *out=result;
}
