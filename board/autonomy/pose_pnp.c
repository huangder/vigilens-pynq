/* Levenberg-Marquardt PnP for the 68-point face, PS-side and dependency free.
 *
 * Model: x right, y down, z into the scene, units = IOD (face68_3d.h).
 * Camera: pinhole, f = image width, principal point at the image centre, matching
 * backend/face_landmark.py and peppa_geometry.solve_pose_pnp.
 *
 * The Jacobian uses a left rotation perturbation (R <- exp(d) R), so dP/dd =
 * -[R X]_x and dP/dt = I. LM damping keeps the normal equations solvable when
 * the pose is near-frontal (rank-deficient in yaw/pitch/t_z combinations).
 */
#include "pose_pnp.h"

#include <math.h>
#include <string.h>

#define PI 3.14159265358979323846
#define ITERATIONS 40

static void rodrigues(const double r[3], double R[9])
{
    double theta = sqrt(r[0] * r[0] + r[1] * r[1] + r[2] * r[2]);
    double kx, ky, kz, c, s, v;
    if (theta < 1e-12) {
        R[0] = 1; R[1] = 0; R[2] = 0;
        R[3] = 0; R[4] = 1; R[5] = 0;
        R[6] = 0; R[7] = 0; R[8] = 1;
        return;
    }
    kx = r[0] / theta; ky = r[1] / theta; kz = r[2] / theta;
    c = cos(theta); s = sin(theta); v = 1.0 - c;
    R[0] = c + kx * kx * v;      R[1] = kx * ky * v - kz * s; R[2] = kx * kz * v + ky * s;
    R[3] = ky * kx * v + kz * s; R[4] = c + ky * ky * v;      R[5] = ky * kz * v - kx * s;
    R[6] = kz * kx * v - ky * s; R[7] = kz * ky * v + kx * s; R[8] = c + kz * kz * v;
}

static void compose_left(const double d[3], const double R[9], double out[9])
{
    double D[9], tmp[9];
    int i, j, k;
    rodrigues(d, D);
    for (i = 0; i < 3; ++i)
        for (j = 0; j < 3; ++j) {
            tmp[i * 3 + j] = 0;
            for (k = 0; k < 3; ++k) tmp[i * 3 + j] += D[i * 3 + k] * R[k * 3 + j];
        }
    memcpy(out, tmp, sizeof(tmp));
}

static int solve6(double A[6][6], double b[6], double x[6])
{
    int i, j, k, pivot;
    for (i = 0; i < 6; ++i) {
        pivot = i;
        for (j = i + 1; j < 6; ++j)
            if (fabs(A[j][i]) > fabs(A[pivot][i])) pivot = j;
        if (fabs(A[pivot][i]) < 1e-12) return -1;
        if (pivot != i) {
            double tmp;
            for (k = 0; k < 6; ++k) { tmp = A[i][k]; A[i][k] = A[pivot][k]; A[pivot][k] = tmp; }
            tmp = b[i]; b[i] = b[pivot]; b[pivot] = tmp;
        }
        for (j = i + 1; j < 6; ++j) {
            double factor = A[j][i] / A[i][i];
            for (k = i; k < 6; ++k) A[j][k] -= factor * A[i][k];
            b[j] -= factor * b[i];
        }
    }
    for (i = 5; i >= 0; --i) {
        double sum = b[i];
        for (j = i + 1; j < 6; ++j) sum -= A[i][j] * x[j];
        x[i] = sum / A[i][i];
    }
    return 0;
}

static double cost_of(const double points[68][2], const double model[68][3],
                      const double R[9], const double t[3], double f, double cx, double cy,
                      double J[68 * 2 * 6], double residual[68 * 2])
{
    double cost = 0.0;
    int i;
    for (i = 0; i < 68; ++i) {
        double X = model[i][0], Y = model[i][1], Z = model[i][2];
        double Px = R[0] * X + R[1] * Y + R[2] * Z + t[0];
        double Py = R[3] * X + R[4] * Y + R[5] * Z + t[1];
        double Pz = R[6] * X + R[7] * Y + R[8] * Z + t[2];
        double u, v, ru, rv;
        if (Pz <= 1e-9) return 1e18;
        u = f * Px / Pz + cx;
        v = f * Py / Pz + cy;
        ru = u - points[i][0];
        rv = v - points[i][1];
        cost += ru * ru + rv * rv;
        if (J) {
            /* dP/d(d) = -[R X]_x */
            double RX = R[0] * X + R[1] * Y + R[2] * Z;
            double RY = R[3] * X + R[4] * Y + R[5] * Z;
            double RZ = R[6] * X + R[7] * Y + R[8] * Z;
            double dP[3][6];
            double du[3], dv[3];
            int k;
            dP[0][0] = 0;     dP[0][1] = RZ;  dP[0][2] = -RY; dP[0][3] = 1; dP[0][4] = 0; dP[0][5] = 0;
            dP[1][0] = -RZ;   dP[1][1] = 0;   dP[1][2] = RX;  dP[1][3] = 0; dP[1][4] = 1; dP[1][5] = 0;
            dP[2][0] = RY;    dP[2][1] = -RX; dP[2][2] = 0;   dP[2][3] = 0; dP[2][4] = 0; dP[2][5] = 1;
            du[0] = f / Pz; du[1] = 0; du[2] = -f * Px / (Pz * Pz);
            dv[0] = 0; dv[1] = f / Pz; dv[2] = -f * Py / (Pz * Pz);
            for (k = 0; k < 6; ++k)
                J[(2 * i) * 6 + k] = du[0] * dP[0][k] + du[1] * dP[1][k] + du[2] * dP[2][k];
            for (k = 0; k < 6; ++k)
                J[(2 * i + 1) * 6 + k] = dv[0] * dP[0][k] + dv[1] * dP[1][k] + dv[2] * dP[2][k];
        }
        if (residual) { residual[2 * i] = ru; residual[2 * i + 1] = rv; }
    }
    return cost;
}

static double refine(const double points[68][2], const double model[68][3], double f,
                     double cx, double cy, double R[9], double t[3], int *ok)
{
    double J[68 * 2 * 6], residual[68 * 2];
    double cost = cost_of(points, model, R, t, f, cx, cy, J, residual);
    double lambda = 1e-3;
    int iter;
    *ok = 0;
    for (iter = 0; iter < ITERATIONS && isfinite(cost); ++iter) {
        double A[6][6], b[6], delta[6], candidate[3], t_candidate[3], R_candidate[9];
        double new_cost;
        int i, k, r, c;
        memset(A, 0, sizeof(A)); memset(b, 0, sizeof(b));
        for (i = 0; i < 68 * 2; ++i) {
            for (r = 0; r < 6; ++r) {
                b[r] -= J[i * 6 + r] * residual[i];
                /* J'J is symmetric. Each surviving entry keeps its original
                 * accumulation order, and multiplication is commutative. */
                for (c = r; c < 6; ++c) A[r][c] += J[i * 6 + r] * J[i * 6 + c];
            }
        }
        for (r = 0; r < 6; ++r)
            for (c = 0; c < r; ++c) A[r][c] = A[c][r];
        for (r = 0; r < 6; ++r) A[r][r] += lambda * (1.0 + A[r][r]);
        if (solve6(A, b, delta)) break;
        /* The former retry loop multiplied lambda without solving delta again;
         * all six attempts evaluated this identical candidate. A rejected
         * step immediately ends refinement in both versions. */
        for (k = 0; k < 3; ++k) { candidate[k] = delta[k]; t_candidate[k] = t[k] + delta[3 + k]; }
        compose_left(candidate, R, R_candidate);
        new_cost = cost_of(points, model, R_candidate, t_candidate, f, cx, cy, NULL, NULL);
        if (!(new_cost < cost)) break;
        memcpy(R, R_candidate, sizeof(R_candidate));
        for (k = 0; k < 3; ++k) t[k] = t_candidate[k];
        {
            double step = sqrt(delta[0]*delta[0]+delta[1]*delta[1]+delta[2]*delta[2]);
            cost = new_cost;
            lambda = fmax(lambda / 3.0, 1e-9);
            if (step < 1e-7) break;
        }
        cost = cost_of(points, model, R, t, f, cx, cy, J, residual);
    }
    *ok = isfinite(cost);
    return cost;
}

static int finish_pose(const double points[68][2],const double model[68][3],
                       const double R[9],const double t[3],double f,double cx,double cy,
                       double iod,double limit,vg_pose *out)
{
    vg_pose result={0};double err=0;int i;
    for(i=0;i<68;++i) {
        double X=model[i][0],Y=model[i][1],Z=model[i][2];
        double Px=R[0]*X+R[1]*Y+R[2]*Z+t[0];
        double Py=R[3]*X+R[4]*Y+R[5]*Z+t[1];
        double Pz=R[6]*X+R[7]*Y+R[8]*Z+t[2];
        if(!(Pz>1e-9))return -1;
        err+=hypot(f*Px/Pz+cx-points[i][0],f*Py/Pz+cy-points[i][1]);
    }
    result.reproj_px=err/68.0;result.reproj_frac=result.reproj_px/iod;
    if(!isfinite(result.reproj_frac)||(limit>0&&result.reproj_frac>limit))return -2;
    memcpy(result.t,t,sizeof(result.t));
    result.yaw=atan2(-R[6],hypot(R[0],R[3]))*180.0/PI;
    result.pitch=atan2(R[7],R[8])*180.0/PI;
    result.roll=atan2(R[3],R[0])*180.0/PI;
    *out=result;return 0;
}

static int seed_valid(const vg_pose_seed *s,int width,int height)
{
    int i,j,k;
    if(!s||!s->valid||s->width!=width||s->height!=height||!(s->t[2]>0))return 0;
    for(i=0;i<9;++i)if(!isfinite(s->R[i]))return 0;
    for(i=0;i<3;++i)if(!isfinite(s->t[i]))return 0;
    /* Numerical state integrity, not a measurement acceptance threshold. */
    for(i=0;i<3;++i)for(j=i;j<3;++j) {
        double dot=0;for(k=0;k<3;++k)dot+=s->R[i*3+k]*s->R[j*3+k];
        if(fabs(dot-(i==j?1.0:0.0))>1e-6)return 0;
    }
    {double det=s->R[0]*(s->R[4]*s->R[8]-s->R[5]*s->R[7])-s->R[1]*(s->R[3]*s->R[8]-s->R[5]*s->R[6])+s->R[2]*(s->R[3]*s->R[7]-s->R[4]*s->R[6]);
     if(fabs(det-1.0)>1e-6)return 0;}
    return 1;
}
static void save_seed(vg_pose_seed *s,const double R[9],const double t[3],int width,int height)
{
    if(s){memcpy(s->R,R,sizeof(s->R));memcpy(s->t,t,sizeof(s->t));s->width=width;s->height=height;s->valid=1;}
}
static int solve_impl(const double points[68][2],int width,int height,
                     const double model[68][3],double reproj_max_frac,vg_pose *out,
                     const vg_pose_seed *previous,vg_pose_seed *next)
{
    double eye_a[2] = {0, 0}, eye_b[2] = {0, 0}, eye_m[2] = {0, 0};
    double model_a[3] = {0, 0, 0}, model_b[3] = {0, 0, 0};
    double iod_img, iod_model, f, cx, cy, Z, best_cost = 1e18, best_t[3] = {0, 0, 0};
    double best_R[9] = {1, 0, 0, 0, 1, 0, 0, 0, 1};
    static const double starts[] = {-30.0, -20.0, -10.0, 0.0, 10.0, 20.0, 30.0};
    int i, k, s;
    if (!points || !model || !out || width <= 0 || height <= 0) return -1;
    for (i = 0; i < 68; ++i)
        if (!isfinite(points[i][0]) || !isfinite(points[i][1]) ||
            !isfinite(model[i][0]) || !isfinite(model[i][1]) || !isfinite(model[i][2]))
            return -1;
    for (i = 36; i < 42; ++i) { eye_a[0] += points[i][0]; eye_a[1] += points[i][1];
                                model_a[0] += model[i][0]; model_a[1] += model[i][1]; model_a[2] += model[i][2]; }
    for (i = 42; i < 48; ++i) { eye_b[0] += points[i][0]; eye_b[1] += points[i][1];
                                model_b[0] += model[i][0]; model_b[1] += model[i][1]; model_b[2] += model[i][2]; }
    for (k = 0; k < 2; ++k) { eye_a[k] /= 6.0; eye_b[k] /= 6.0; }
    for (k = 0; k < 3; ++k) { model_a[k] /= 6.0; model_b[k] /= 6.0; }
    eye_m[0] = 0.5 * (eye_a[0] + eye_b[0]);
    eye_m[1] = 0.5 * (eye_a[1] + eye_b[1]);
    iod_img = hypot(eye_b[0] - eye_a[0], eye_b[1] - eye_a[1]);
    iod_model = sqrt((model_b[0] - model_a[0]) * (model_b[0] - model_a[0]) +
                     (model_b[1] - model_a[1]) * (model_b[1] - model_a[1]) +
                     (model_b[2] - model_a[2]) * (model_b[2] - model_a[2]));
    if (!(iod_img > 1e-6) || !(iod_model > 1e-9)) return -1;
    if (fabs(points[0][0] - points[16][0]) < 1e-6 &&
        fabs(points[0][1] - points[16][1]) < 1e-6) return -1;
    f = (double)width; cx = width / 2.0; cy = height / 2.0;
    Z = f * iod_model / iod_img;
    if(reproj_max_frac>0&&seed_valid(previous,width,height)) {
        double R[9],t[3],cost;int ok;
        memcpy(R,previous->R,sizeof(R));memcpy(t,previous->t,sizeof(t));
        cost=refine(points,model,f,cx,cy,R,t,&ok);
        if(ok&&cost<1e17&&!finish_pose(points,model,R,t,f,cx,cy,iod_img,reproj_max_frac,out)) {
            save_seed(next,R,t,width,height);return 0;
        }
    }
    /* Multi-start in yaw: a single frontal start falls into different local minima
     * than the OpenCV reference on turned faces (17/605 mismatches before this). */
    for (s = 0; s < (int)(sizeof(starts) / sizeof(starts[0])); ++s) {
        double rvec[3] = {0, starts[s] * PI / 180.0, 0}, R[9];
        double t[3];
        double cost;
        int ok;
        t[0] = (eye_m[0] - cx) * Z / f;
        t[1] = (eye_m[1] - cy) * Z / f;
        t[2] = Z;
        rodrigues(rvec, R);
        cost = refine(points, model, f, cx, cy, R, t, &ok);
        if (ok && cost < best_cost) {
            best_cost = cost;
            memcpy(best_R, R, sizeof(best_R));
            memcpy(best_t, t, sizeof(best_t));
        }
    }
    if (!(best_cost < 1e17)) return -1;
    {int rc=finish_pose(points,model,best_R,best_t,f,cx,cy,iod_img,reproj_max_frac,out);if(rc)return rc;}
    save_seed(next,best_R,best_t,width,height);
    return 0;
}
int vg_pose_solve(const double points[68][2],int width,int height,
                  const double model[68][3],double limit,vg_pose *out)
{return solve_impl(points,width,height,model,limit,out,NULL,NULL);}
int vg_pose_track(const double points[68][2],int width,int height,
                  const double model[68][3],double limit,const vg_pose_seed *previous,
                  vg_pose_seed *next,vg_pose *out)
{
    vg_pose_seed copy={0};if(previous)copy=*previous;
    if(next)next->valid=0;
    return solve_impl(points,width,height,model,limit,out,&copy,next);
}
