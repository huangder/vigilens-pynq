/* PnP CLI for board/host parity: reads 68 pixel points, prints one pose line.
 * Format: first line "width height", then 68 lines "x y". Deterministic, no I/O
 * beyond stdin/stdout; used to compare the A9 build against the x86 build. */
#include "pose_pnp.h"
#include "face68_3d.h"
#include <stdio.h>
#include <stdlib.h>

int main(void)
{
    double points[68][2];
    int width, height, i, frame = 0, solved = 0;
    if (scanf("%d %d", &width, &height) != 2) return 2;
    for (;;) {
        vg_pose pose;
        int rc;
        for (i = 0; i < 68; ++i)
            if (scanf("%lf %lf", &points[i][0], &points[i][1]) != 2) {
                if (i == 0) return solved ? 0 : 2;
                return 2;  /* truncated set */
            }
        rc = vg_pose_solve(points, width, height, VG_FACE68_MODEL, 0.25, &pose);
        if (rc == 0) {
            printf("POSE frame=%d yaw=%.4f pitch=%.4f roll=%.4f reproj_px=%.4f reproj_frac=%.4f\n",
                   frame, pose.yaw, pose.pitch, pose.roll, pose.reproj_px, pose.reproj_frac);
            ++solved;
        } else {
            printf("POSE frame=%d rc=%d\n", frame, rc);
        }
        ++frame;
    }
}
