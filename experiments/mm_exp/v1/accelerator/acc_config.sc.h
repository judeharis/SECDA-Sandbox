#ifndef ACC_CONFIG_H
#define ACC_CONFIG_H

#define ACCNAME MM_ACC
#define SUBMODULENAME acc_pe

//==============================================================================
// Hardware Constants
//==============================================================================
// Define any Hardware specific constants for the accelerator
// These constants will be accessible in the driver
// These constants will be used to generate the hardware

// OP-Code Stuct
// 0000 : 0 = NOP;
// 0001 : 1 = read_A;
// 0010 : 2 = read_B;
// 0011 : 3 = read_A -> read_B;
// 0100 : 4 = compute_C;
// 0101 : 5 = read_A -> compute_C;
// 0110 : 6 = read_B -> compute_C;
// 0111 : 7 = read_A -> read_B -> compute_C;

// 1000 : 8 = send_C;
// 1001 : 9 = read_A -> send_C;
// 1010 : 10 = read_B -> send_C;
// 1011 : 11 = read_A -> read_B -> send_C;
// 1100 : 12 = compute_C -> send_C;
// 1101 : 13 = read_A -> compute_C -> send_C;
// 1110 : 14 = read_B -> compute_C -> send_C;
// 1111 : 15 = read_A -> read_B -> compute_C -> send_C;

//==============================================================================
// Address mapping for the accelerator and DMA
//==============================================================================
#ifdef KRIA
// KRIA
// Pre-Defined Address for Accelerator
#define acc_ctrl_address 0xA0000000
#define acc_hwc_address 0xA0050000
#define dma_addr0 0x00A0010000
#define dma_addr1 0x00A0020000
#define dma_addr2 0x00A0030000
#define dma_addr3 0x00A0040000

#define DMA_BL 4194304
#define DMA_RANGE_START 0x0000000037400000
#define DMA_RANGE_END 0x00000000773FFFFF
#define DMA_RANGE_OFFSET 0xC00000         // 1.5MB
#define DMA_RANGE_SIZE 0x0000000040000000 // 1GB
#define DMA_IN_BUF_SIZE 0x8000000         // 128MB
#define DMA_OUT_BUF_SIZE 0x4000000        // 64MB

#define dma_in0 0x38000000
#define dma_in1 0x3A000000
#define dma_in2 0x3C000000
#define dma_in3 0x3E000000

#define dma_out0 0x39000000
#define dma_out1 0x3B000000
#define dma_out2 0x3D000000
#define dma_out3 0x40000000
#else
// Z1
// Pre-Defined Address for Accelerator
#define acc_ctrl_address 0x43C00000
#define acc_hwc_address 0x43C10000
#define dma_addr0 0x40400000
#define dma_in0 0x18000000
#define dma_out0 0x1C000000

#define DMA_IN_BUF_SIZE 0x0800000 // 8MB
#define DMA_OUT_BUF_SIZE 0x0800000 // 8MB
#define DMA_INP_SIZE 0x100000
#define DMA_WGT_SIZE (DMA_IN_BUF_SIZE - DMA_INP_SIZE)
#define DMA_RANGE_START 0x18000000
#define DMA_RANGE_END 0x1fffffff
#define DMA_RANGE_SIZE 0x8000000
#endif // KRIA

// AXIMM Constants
#ifdef KRIA
#define MM_BL 0x100000 // 1MB
#define in_addr 0x38000000
#define out_addr 0x39000000
#else
// Z1
#define MM_BL 0x100000 // 1MB
#define in_addr 0x18000000
#define out_addr 0x19000000
#endif

// AXIMM Constants
#ifdef KRIA
#define MM_BL 0x100000 // 1MB
#define in_addr 0x38000000
#define out_addr 0x39000000
#else
// Z1
#define MM_BL 0x100000 // 1MB
#define in_addr 0x18000000
#define out_addr 0x19000000
#endif

//==============================================================================
// Data types
//==============================================================================
#define ACC_DTYPE sc_int
#define ACC_C_DTYPE int
#define AXI_DWIDTH 32
#define AXI_TYPE sc_uint
#define s_mdma multi_dma<AXI_DWIDTH, 0>
#define mm_buf mm_buffer<unsigned long long>
#define mm_buf_float mm_buffer<float>

#define a_ctrl acc_ctrl<int>
#define h_ctrl hwc_ctrl<int>

//==============================================================================
// ACC Specific Constants
//==============================================================================

// Buffer sizes
const int A_buffer_size = 4096;
const int B_buffer_size = 4096;
const int C_buffer_size = 4096;

const int tile_M = 16;
const int tile_N = 16;
const int tile_K = 16;

const int M_Unroll = 4;
const int N_Unroll = 4;
const int K_Unroll = 4;

// ACC Specific Constants
#define STOPPER -1

#define HWC_Monitor_Count 3
#define CTRL_Reg_Count 1

// Number of PEs
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

#ifdef VERBOSE_ACC
#define ALOG(x) std::cout << x << std::endl
#else // !VERBOSE_ACC
#define ALOG(x)
#endif

typedef _BDATA<AXI_DWIDTH, AXI_TYPE> ADATA;

#else // __SYNTHESIS__
#include "sysc_types.h"
#define ALOG(x)

struct _NDATA {
  AXI_TYPE<AXI_DWIDTH> data;
  bool tlast;
  inline friend ostream &operator<<(ostream &os, const _NDATA &v) {
    cout << "data&colon; " << v.data << " tlast: " << v.tlast;
    return os;
  }
};

typedef _NDATA ADATA;
#endif

//==============================================================================
// HW Structs
//==============================================================================

struct opcode {
  unsigned int packet;
  bool read_A;
  bool read_B;
  bool compute_C;
  bool send_C;

  opcode(sc_uint<32> _packet) {
    ALOG("OPCODE: " << _packet);
    ALOG("Time: " << sc_time_stamp());
    packet = _packet;
    read_A = _packet.range(0, 0);
    read_B = _packet.range(1, 1);
    compute_C = _packet.range(2, 2);
    send_C = _packet.range(3, 3);
  }
};

struct code_extension {
  int N;
  int M;
  int K;

  code_extension(sc_uint<32> _packetA, sc_uint<32> _packetB,
                 sc_uint<32> _packetC) {
    N = _packetA;
    M = _packetB;
    K = _packetC;
    ALOG("Time: " << sc_time_stamp());
    ALOG("N: " << N << ", M: " << M << ", K: " << K);
  }
};

//==============================================================================
// HW Submodule Construction SIM/HW Structs
//==============================================================================

//==============================================================================

#endif // defined(SYSC) || defined(__SYNTHESIS__)
#endif // ACC_CONFIG_H