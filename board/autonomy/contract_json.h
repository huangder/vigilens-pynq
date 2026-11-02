#ifndef VG_CONTRACT_JSON_H
#define VG_CONTRACT_JSON_H
#include <stddef.h>
#include <stdio.h>
#include "measure.h"
#include "decision.h"
#ifdef __cplusplus
extern "C" {
#endif

/* One contract frame, serialized on the PS so the board can publish JSON itself.
 * Only the nine top-level fields from docs/interface.md §1 are written; the
 * caller owns capture/model/PL provenance, as it does for vg_measure_update.
 * status/advice/reason come from vg_decision; they are UTF-8 and escaped here. */
typedef struct {
    int bbox[4];            /* x, y, w, h in source pixels, integers */
    double yaw, pitch, roll;/* degrees; vg_decision only consumes yaw/pitch */
    double visible;         /* 0..1, must equal the measure input's face.visible */
} vg_contract_face;

/* Writes one JSON object WITHOUT a trailing newline.
 * Returns 0 on success, -1 on NULL/invalid/insufficient capacity. */
int vg_contract_line(char *buffer, size_t capacity,
                     const vg_measure_result *result,
                     const vg_contract_face *face,
                     const vg_vitals *vital);
/* Same object plus '\n' to `stream`. Returns 0, or -1 on write/serialization. */
int vg_contract_write(FILE *stream, const vg_measure_result *result,
                      const vg_contract_face *face,
                      const vg_vitals *vital);
#ifdef __cplusplus
}
#endif
#endif
