

#ifndef ACC_DRIVER
#define ACC_DRIVER

#include "acc_container.h"

#define DLOG(X)

namespace acc_driver {

void ACC_Offload(acc_container &drv) {

  // Problem specific parameters
  int M = drv.M; // M dim size
  int N = drv.N; // N dim size
  int K = drv.K; // K dim size

  int *A = drv.A;
  int *B = drv.B;
  int *C = drv.C;

#ifndef block_M
  // Block M: tiling factor for dim M, after taking to account size of B and C
  // buffers, Block K and N, and compute tile size for K
  int block_M =
      std::min(C_buffer_size / tile_N, std::min(A_buffer_size / tile_K, M));
#endif

#ifndef block_N
  // Block N: tiling factor for dim N, after taking into account the size of A
  // and C buffers, and compute tile size for M and K
  int block_N =
      std::min(C_buffer_size / block_M, std::min(B_buffer_size / tile_K, N));
#endif

#ifndef block_K
  // Block K: tiling factor for dim K, after taking into account: size of A and
  // B buffers, and compute tile size for N and M
  int block_K =
      std::min(B_buffer_size / block_M, std::min(A_buffer_size / block_N, K));
#endif

  drv.hwc->set_target_state(0, 3);
  drv.hwc->set_target_state(1, 2);
  drv.hwc->set_target_state(2, 2);
  drv.hwc->reset_hwc();

  // Start Tiling
  for (int m = 0; m < M; m += block_M) {
    for (int n = 0; n < N; n += block_N) {
      for (int k = 0; k < K; k += block_K) {
        // Gets pointer to DMA_IN_BUFFER
        int *dma_inbuffer = drv.mdma->dmas[0].dma_get_inbuffer();

        // Data_len is used to track what is in the DMA_IN_BUFFER
        int data_len = 0;

        // Encodes HEADER; Tells accelerator to expect A, B tiles and compute C
        uint32_t op_code = 7;
        uint32_t ce_a = block_N;
        uint32_t ce_b = block_M;
        uint32_t ce_c = block_K;
        dma_inbuffer[data_len++] = op_code;
        dma_inbuffer[data_len++] = ce_a;
        dma_inbuffer[data_len++] = ce_b;
        dma_inbuffer[data_len++] = ce_c;

        // Copies A into DMA_IN_BUFFER; Increments data_len by length of A
        for (int tm = 0; tm < block_M; tm++)
          for (int tk = 0; tk < block_K; tk++)
            dma_inbuffer[data_len + block_K * tm + tk] =
                A[(m + tm) * K + (k + tk)];
        data_len += block_M * block_K;

        // Copies B into DMA_IN_BUFFER; Increments data_len by length of B
        for (int tk = 0; tk < block_K; tk++)
          for (int tn = 0; tn < block_N; tn++)
            dma_inbuffer[data_len + block_N * tk + tn] =
                B[(k + tk) * N + (n + tn)];
        data_len += block_K * block_N;

        // Sends data_len of data
        drv.mdma->dmas[0].dma_start_send(data_len);

        // Waits for data to transfer to finish
        drv.mdma->dmas[0].dma_wait_send();
      }
      int *dma_inbuffer = drv.mdma->dmas[0].dma_get_inbuffer();
      int data_len = 0;

      // Encodes HEADER; Tells accelerator to expect send C
      uint32_t op_code = 8;
      uint32_t ce_a = block_N;
      uint32_t ce_b = block_M;
      uint32_t ce_c = block_K;

      dma_inbuffer[data_len++] = op_code;
      dma_inbuffer[data_len++] = ce_a;
      dma_inbuffer[data_len++] = ce_b;
      dma_inbuffer[data_len++] = ce_c;
      drv.mdma->dmas[0].dma_start_send(data_len);
      drv.mdma->dmas[0].dma_wait_send();

      // Indicates to DMA, how much space is available and where it is
      drv.mdma->dmas[0].dma_start_recv(block_M * block_N);

      // Waits for data to be received (including TLAST signal)
      drv.mdma->dmas[0].dma_wait_recv();

      // Gets pointer to DMA_OUT_BUFFER
      int *dma_outbuffer = drv.mdma->dmas[0].dma_get_outbuffer();

      // Copies result from DMA_OUT_BUFFER to padded output buffer

      for (int tm = 0; tm < block_M; tm++) {
        for (int tn = 0; tn < block_N; tn++) {
          // cout << "C Index: " << (m + tm) * N + n + tn << endl;
          // cout << "C Value: " << dma_outbuffer[block_N * tm + tn] << endl;
          C[(m + tm) * N + n + tn] += dma_outbuffer[block_N * tm + tn];
        }
      }
    }
  }
  drv.hwc->print_hwc_map(true);
  drv.ctrl->print_reg_map(true);
}

void Entry(acc_container &drv) { ACC_Offload(drv); }

} // namespace acc_driver

#endif // ACC_DRIVER