
// hello world c++ program

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

int main() {
  // ========================================
  // ========================================
  // Initialize the Accelerator

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
  DELLOG(std::cout << "ACCNAME Accelerator";);
  DELLOG(std::cout << std::endl;);
  DELLOG(std::cout << "===========================" << std::endl;);

  // ========================================
  // ========================================
  // Define problem parameters
  int M = 64; // M dim size
  int N = 64; // N dim size
  int K = 64; // K dim size

  std::vector<int> A_vec(M * K, 1);
  std::vector<int> B_vec(K * N, 1);
  std::vector<int> C_vec(M * N, 0);

  // ========================================
  // ========================================
  // FPGA Impl
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

  drv.M = M;
  drv.N = N;
  drv.K = K;

  drv.A = A_vec.data();
  drv.B = B_vec.data();
  drv.C = C_vec.data();

  // Call FPGA driver
  prf_start(1);
  acc_driver::Entry(drv);
  prf_end(1, a_t.fpga_total);
  DELLOG(cout << "FPGA Done!" << endl;);

  // Optional: verify against a CPU reference
  std::vector<int> C_ref(M * N, 0);
  for (int i = 0; i < M; ++i)
    for (int k = 0; k < K; ++k)
      for (int j = 0; j < N; ++j)
        C_ref[i * N + j] += A_vec[i * K + k] * B_vec[k * N + j];

  bool ok = true;
  for (int idx = 0; idx < M * N; ++idx) {
    if (C_ref[idx] != C_vec[idx]) {
      ok = false;
      break;
    }
  }
  std::cout << (ok ? "Validation: PASSED\n" : "Validation: FAILED\n");
  a_t.print();

  int return_code = ok ? 0 : 1;
  return return_code;
}
