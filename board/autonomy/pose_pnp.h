#ifndef VG_POSE_PNP_H
#define VG_POSE_PNP_H
#ifdef __cplusplus
extern "C" {
#endif

/* 68-point head pose on the PS, no OpenCV.
 *
 * The reference model is `face68_3d.h` (generated from face68_3d.json): x right,
 * y down, z into the scene, units = inter-ocular distance. The solver minimises
 * reprojection with Levenberg-Marquardt from a frontal initialisation; it is the
 * C counterpart of board/autonomy/peppa_geometry.py::solve_pose_pnp and is
 * validated against it, not against unmeasured "accuracy". */
typedef struct {
    double yaw, pitch, roll;   /* degrees, same decomposition as the Python path */
    double t[3];               /* translation in model units */
    double reproj_px;          /* mean per-point reprojection error */
    double reproj_frac;        /* reproj_px / inter-ocular distance */
} vg_pose;

/* Current-image refinement seed, never an output pose for a missing frame.
 * Failed solves clear valid. Width/height changes force a cold solve. */
typedef struct {
    double R[9],t[3];
    int width,height,valid;
} vg_pose_seed;

/* 0 = solved, -1 = invalid/degenerate input, -2 = rejected by the reprojection
 * gate (reproj_frac > limit). `out` is only written on success. */
int vg_pose_solve(const double points[68][2], int width, int height,
                  const double model[68][3], double reproj_max_frac,
                  vg_pose *out);
int vg_pose_track(const double points[68][2],int width,int height,
                  const double model[68][3],double reproj_max_frac,
                  const vg_pose_seed *previous,vg_pose_seed *next,vg_pose *out);

#ifdef __cplusplus
}
#endif
#endif
