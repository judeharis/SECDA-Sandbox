#ifndef ACC_CONTAINER
#define ACC_CONTAINER

#include <cassert>
#include <iomanip>
#include <vector>

#ifdef SYSC
#include "systemc_binding.h"
#endif

#include "../acc_config.sc.h"
#include "secda_tools/axi_support/v5/axi_api_v5.h"
#include "secda_tools/secda_profiler/profiler.h"
#include "secda_tools/secda_utils/multi_threading.h"
#include "secda_tools/secda_utils/utils.h"

using namespace std;
using namespace std::chrono;
#define TSCALE microseconds
#define TSCAST duration_cast<nanoseconds>

struct acc_times {
  duration_ns fpga_total;
  duration_ns cpu_total;
  duration_ns driver;

  void print() {
#ifdef ACC_PROFILE
    cout << "================================================" << endl;
    prf_out(TSCALE, fpga_total);
    prf_out(TSCALE, cpu_total);
    prf_out(TSCALE, driver);
    cout << "================================================" << endl;
#endif
  }
};

struct offload_details {
  int count = 0;
  bool profile = false;
};

struct acc_container {
#ifdef SYSC
  ACCNAME *acc;
  struct sysC_sigs *scs;
#else
  int *acc;
#endif

  struct a_ctrl *ctrl;
  struct h_ctrl *hwc;
  struct s_mdma *mdma;
  Profile *profile;

  int *W;
  int *X;
  int *Z;

  struct offload_details t;
  struct acc_times *a_t;
};

#endif // ACC_CONTAINER

