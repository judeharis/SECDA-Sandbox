
#ifndef ACC_DRIVER
#define ACC_DRIVER

#include "acc_container.h"

#define DLOG(X)

namespace acc_driver {

void ACC_Offload(acc_container &drv) {
  int *W = drv.W;
  int *X = drv.X;
  int *Z = drv.Z;

  drv.hwc->set_target_state(0, 3);
  drv.hwc->set_target_state(1, 2);
  drv.hwc->set_target_state(2, 2);
  drv.hwc->reset_hwc();

  int *dma_inbuffer = drv.mdma->dmas[0].dma_get_inbuffer();
  int data_len = 0;

  // load_W | load_X | compute_Z | send_Z
  uint32_t op_code = 15;
  dma_inbuffer[data_len++] = op_code;
  // fixed-dim: dummy code_extension word
  dma_inbuffer[data_len++] = 0;

  const int W_len = IC * OC * KW * KH;
  const int X_len = IC * IW * IH;
  const int Z_len = OC * OW * OH;

  for (int i = 0; i < W_len; i++) dma_inbuffer[data_len++] = W[i];
  for (int i = 0; i < X_len; i++) dma_inbuffer[data_len++] = X[i];

  drv.mdma->dmas[0].dma_start_send(data_len);
  drv.mdma->dmas[0].dma_wait_send();

  drv.mdma->dmas[0].dma_start_recv(Z_len);
  drv.mdma->dmas[0].dma_wait_recv();

  int *dma_outbuffer = drv.mdma->dmas[0].dma_get_outbuffer();
  for (int i = 0; i < Z_len; i++) Z[i] = dma_outbuffer[i];

  drv.hwc->print_hwc_map(true);
  drv.ctrl->print_reg_map(true);
}

void Entry(acc_container &drv) { ACC_Offload(drv); }

} // namespace acc_driver

#endif // ACC_DRIVER

