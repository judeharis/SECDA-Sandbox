#ifndef ACC_CONFIG_H
#define ACC_CONFIG_H

#define ACCNAME CONV2D_ACC
#define SUBMODULENAME acc_pe

// bit0: load_W, bit1: load_X, bit2: compute_Z, bit3: send_Z

//==============================================================================
// Address mapping for the accelerator and DMA
//==============================================================================
#ifdef KRIA
#define acc_ctrl_address 0xA0000000
#define acc_hwc_address 0xA0020000
#define acc_address 0x00A0000000
#define dma_addr0 0xA0010000
#define dma_in0 0x3A000000
#define dma_out0 0x38000000

#define DMA_BL 4194304
#define DMA_RANGE_START 0x0000000037400000
#define DMA_RANGE_END 0x00000000773FFFFF
#define DMA_RANGE_OFFSET 0xC00000
#define DMA_RANGE_SIZE 0x40000000

#define DMA_IN_BUF_SIZE 0x8000000
#define DMA_OUT_BUF_SIZE 0x0800000
#define DMA_INP_SIZE 0x100000
#define DMA_WGT_SIZE (DMA_IN_BUF_SIZE - DMA_INP_SIZE)
#else
// Z1
#define acc_ctrl_address 0x43C00000
#define acc_hwc_address 0x43C10000
#define dma_addr0 0x40400000
#define dma_in0 0x18000000
#define dma_out0 0x1C000000

#define DMA_IN_BUF_SIZE 0x0800000
#define DMA_OUT_BUF_SIZE 0x0800000
#define DMA_INP_SIZE 0x100000
#define DMA_WGT_SIZE (DMA_IN_BUF_SIZE - DMA_INP_SIZE)
#define DMA_RANGE_START 0x18000000
#define DMA_RANGE_END 0x1fffffff
#define DMA_RANGE_SIZE 0x8000000
#endif

//==============================================================================
// Data types
//==============================================================================
#define ACC_DTYPE sc_int
#define ACC_C_DTYPE int
#define AXI_DWIDTH 32
#define AXI_TYPE sc_uint
#define s_mdma multi_dma<AXI_DWIDTH, 0>

#define a_ctrl acc_ctrl<int>
#define h_ctrl hwc_ctrl<int>

//==============================================================================
// Fixed dimensions (defaults from arch-base prompt)
//==============================================================================
const int IC = 8;
const int OC = 8;
const int KW = 3;
const int KH = 3;
const int IW = 16;
const int IH = 16;
const int OW = IW - KW + 1;
const int OH = IH - KH + 1;

// Buffers (words)
const int W_buffer_size = 4096;
const int X_buffer_size = 4096;
const int Z_buffer_size = 4096;

// Bounded parallelism knobs (DSE-safe)
const int OC_UNROLL = 4;
const int IC_UNROLL = 1;

#define STOPPER -1
#define HWC_Monitor_Count 3
#define CTRL_Reg_Count 1
#define ADD_PE_COUNT 0

//==============================================================================
// SystemC Specfic SIM/HW Configurations
//==============================================================================
#if defined(SYSC) || defined(__SYNTHESIS__)
#include <systemc.h>

#ifndef __SYNTHESIS__
#include "secda_tools/axi_support/v5/axi_api_v5.h"
#include "secda_tools/secda_integrator/sysc_types.h"
#include "secda_tools/secda_profiler/profiler.h"
#define DWAIT(x) wait(x)
typedef _BDATA<AXI_DWIDTH, AXI_TYPE> ADATA;
#else
#include "sysc_types.h"
struct _NDATA {
  AXI_TYPE<AXI_DWIDTH> data;
  bool tlast;
};
typedef _NDATA ADATA;
#endif

struct opcode {
  unsigned int packet;
  bool load_W;
  bool load_X;
  bool compute_Z;
  bool send_Z;
  opcode(sc_uint<32> _packet) {
    packet = _packet;
    load_W = _packet.range(0, 0);
    load_X = _packet.range(1, 1);
    compute_Z = _packet.range(2, 2);
    send_Z = _packet.range(3, 3);
  }
};

// Fixed-dimension design: code_extension is a placeholder for protocol compatibility
struct code_extension {
  code_extension(sc_uint<32>) {}
};

#endif
#endif

