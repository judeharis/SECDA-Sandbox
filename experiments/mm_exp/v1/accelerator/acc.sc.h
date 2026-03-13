#ifndef ACCNAME_H
#define ACCNAME_H

#include "acc_config.sc.h"
#include "hwc.sc.h"
#include <systemc.h>

SC_MODULE(ACCNAME) {
  sc_in<bool> clock;
  sc_in<bool> reset;

  // ================================================= //
  // Global ports
  // ================================================= //

  // Control ports
  CTRL_Define_Ports;

  // Data ports
  sc_fifo_in<ADATA> din1;
  sc_fifo_out<ADATA> dout1;

  sc_out<unsigned int> testS;

  // ================================================= //
  // Global variables
  // ================================================= //

  sc_signal<int> M; // M dim size
  sc_signal<int> N; // N dim size
  sc_signal<int> K; // K dim size
  code_extension acc_args = code_extension(0, 0, 0);

  // ================================================= //
  // Global buffers
  // ================================================= //
  sc_int<32> A_buffer[4096];
  sc_int<32> B_buffer[4096];
  sc_int<32> C_buffer[4096];

  // ================================================= //
  // Global signals
  // ================================================= //
  DEFINE_SC_SIGNAL(bool, compute)
  DEFINE_SC_SIGNAL(bool, send)

  // ================================================= //
  // Profiling variable
  // ================================================= //

  // ================================================= //
  // Functions
  // ================================================= //

  void compute_tile(int, int, int, int, int);

  // ================================================= //
  // HWC
  // ================================================= //

  HWC_Reset;
  HWC_CTHREAD(Recv)
  HWC_CTHREAD(Compute)
  HWC_CTHREAD(Send)

  void HW_MAIN() {
    wait();
    while (true) {
      {
#pragma HLS LATENCY max = 0 min = 0
#pragma HLS protocol fixed
        HWC_Logic(Recv);
        HWC_Logic(Compute);
        HWC_Logic(Send);
        DWAIT();
      }
    }
  }
  // ================================================= //

  SC_HAS_PROCESS(ACCNAME);

  ACCNAME(sc_module_name name_) : sc_module(name_) {

    SC_CTHREAD(Recv, clock.pos());
    reset_signal_is(reset, true);

    SC_CTHREAD(Compute, clock.pos());
    reset_signal_is(reset, true);

    SC_CTHREAD(Send, clock.pos());
    reset_signal_is(reset, true);

    SC_CTHREAD(HW_MAIN, clock);
    reset_signal_is(reset, true);

    // clang-format off
CTRL_PragGroup;
CTRL_Prag(testS);

AXI4S_In_Prag(din1);
AXI4S_Out_Prag(dout1);

HWC_PragReset;
HWC_PragGroup(Recv);
HWC_PragGroup(Compute)
HWC_PragGroup(Send);
    // clang-format on
  }
};

#endif