// Conv2D Experiment (SECDA-native template; fixed dims)

#include <fstream>
#include <iostream>

#ifdef SYSC
#include "secda_tools/secda_integrator/systemc_integrate.h"
#endif

#include "accelerator/driver/driver.h"
#include "secda_tools/secda_profiler/profiler.h"

unsigned int dma_addrs[1] = {dma_addr0};
unsigned int dma_addrs_in[1] = {dma_in0};
unsigned int dma_addrs_out[1] = {dma_out0};
struct acc_times a_t;
static struct Profile profile;

#define DELLOG(X) X

#ifdef SYSC
ACCNAME *acc;
struct sysC_sigs *scs;
struct s_mdma *mdma;
#else
int *acc;
struct s_mdma *mdma;
#endif

struct a_ctrl *ctrl;
static h_ctrl *hwc;

using namespace std;

static inline int idx_w(int ic, int oc, int kw, int kh) {
  return (((ic * OC + oc) * KW + kw) * KH + kh);
}
static inline int idx_x(int ic, int iw, int ih) { return ((ic * IW + iw) * IH + ih); }
static inline int idx_z(int oc, int ow, int oh) { return ((oc * OW + ow) * OH + oh); }

int main() {
  DELLOG(std::cout << "===========================" << std::endl;);
#ifdef SYSC
  static ACCNAME _acc("ACCNAME");
  static struct sysC_sigs scs1(1);
  static struct a_ctrl ctrl1;
  static struct h_ctrl hwc1;
  static struct s_mdma mdma1(1, dma_addrs, dma_addrs_in, dma_addrs_out,
                             DMA_IN_BUF_SIZE, DMA_OUT_BUF_SIZE);

  sysC_init();
  hwc1.init_hwc(HWC_Monitor_Count);
  ctrl1.init_sigs(CTRL_Reg_Count);

  sysC_binder(&_acc, &scs1, &ctrl1, &hwc1, &mdma1);
  acc = &_acc;
  scs = &scs1;
  ctrl = &ctrl1;
  hwc = &hwc1;
  mdma = &mdma1;
  DELLOG(std::cout << "Initialised the SystemC Modules" << std::endl;);

#else
  acc = getAccBaseAddress<int>(acc_ctrl_address, 65536);
  int *acc_ctrl_base = getAccBaseAddress<int>(acc_ctrl_address, 65536);
  int *acc_hwc_base = getAccBaseAddress<int>(acc_hwc_address, 65536);
  static struct a_ctrl ctrl1(acc_ctrl_base);
  static struct h_ctrl hwc1(acc_hwc_base);
  static struct s_mdma mdma1(1, dma_addrs, dma_addrs_in, dma_addrs_out,
                             DMA_IN_BUF_SIZE, DMA_OUT_BUF_SIZE);
  ctrl1.init_sigs(CTRL_Reg_Count);
  hwc1.init_hwc(HWC_Monitor_Count);
  ctrl = &ctrl1;
  hwc = &hwc1;
  mdma = &mdma1;
  DELLOG(std::cout << "Initialised the DMA" << std::endl;);
#endif
  DELLOG(std::cout << "CONV2D_ACC Accelerator" << std::endl;);
  DELLOG(std::cout << "===========================" << std::endl;);

  std::vector<int> W(IC * OC * KW * KH);
  std::vector<int> X(IC * IW * IH);
  std::vector<int> Z(OC * OW * OH, 0);

  for (int i = 0; i < (int)W.size(); i++) W[i] = 1;
  for (int i = 0; i < (int)X.size(); i++) X[i] = 1;

  acc_container drv;
#ifdef SYSC
  drv.scs = scs;
#endif
  drv.profile = &profile;
  drv.acc = acc;
  drv.ctrl = ctrl;
  drv.a_t = &a_t;
  drv.hwc = hwc;
  drv.mdma = mdma;

  drv.W = W.data();
  drv.X = X.data();
  drv.Z = Z.data();

  prf_start(1);
  acc_driver::Entry(drv);
  prf_end(1, a_t.fpga_total);
  DELLOG(cout << "FPGA Done!" << endl;);

  // CPU reference (padding=0 stride=1 dilation=1)
  std::vector<int> Z_ref(OC * OW * OH, 0);
  for (int oc = 0; oc < OC; oc++) {
    for (int ow = 0; ow < OW; ow++) {
      for (int oh = 0; oh < OH; oh++) {
        int accv = 0;
        for (int ic = 0; ic < IC; ic++) {
          for (int kw = 0; kw < KW; kw++) {
            for (int kh = 0; kh < KH; kh++) {
              int iw = ow + kw;
              int ih = oh + kh;
              accv += W[idx_w(ic, oc, kw, kh)] * X[idx_x(ic, iw, ih)];
            }
          }
        }
        Z_ref[idx_z(oc, ow, oh)] = accv;
      }
    }
  }

  bool ok = true;
  for (int i = 0; i < (int)Z.size(); i++) {
    if (Z[i] != Z_ref[i]) {
      ok = false;
      break;
    }
  }
  std::cout << (ok ? "Validation: PASSED\n" : "Validation: FAILED\n");
  a_t.print();

  return ok ? 0 : 1;
}

