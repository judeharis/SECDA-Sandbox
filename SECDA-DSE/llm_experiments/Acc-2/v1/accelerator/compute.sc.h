void ACCNAME::Recv() {
  done.write(0);
  compute.write(false);
  send.write(false);
  Recv_si.write(0);
  wait();
  while (1) {
    Recv_si.write(1);
    wait();

    opcode packet(din1.read().data);
    // fixed-dim: read and ignore one code-extension word
    (void)din1.read().data;

    if (packet.load_W) {
      unsigned int read_length = IC * OC * KW * KH;
      for (int i = 0; i < read_length; i++) {
        W_buffer[i] = din1.read().data;
        DWAIT();
      }
    }

    if (packet.load_X) {
      unsigned int read_length = IC * IW * IH;
      for (int i = 0; i < read_length; i++) {
        X_buffer[i] = din1.read().data;
        DWAIT();
      }
    }

    if (packet.compute_Z) {
      compute.write(true);
      wait();
    }

    Recv_si.write(2);
    while (compute) wait();

    if (packet.send_Z) {
      send.write(true);
      wait();
    }

    Recv_si.write(3);
    while (send) wait();

    wait();
  }
}

void ACCNAME::Compute() {
  Compute_si.write(0);
  wait();
  while (1) {
    Compute_si.write(1);
    while (!compute) wait();
    Compute_si.write(2);
    DWAIT();

    for (int oc = 0; oc < OC; oc++) {
      for (int ow = 0; ow < OW; ow++) {
        for (int oh = 0; oh < OH; oh++) {
#pragma HLS PIPELINE II = 1
          int acc = 0;
          for (int ic = 0; ic < IC; ic++) {
#pragma HLS UNROLL factor = IC_UNROLL
            for (int kw = 0; kw < KW; kw++) {
              for (int kh = 0; kh < KH; kh++) {
                int iw = ow + kw;
                int ih = oh + kh;
                int w_idx = (((ic * OC + oc) * KW + kw) * KH + kh);
                int x_idx = ((ic * IW + iw) * IH + ih);
                acc += (int)W_buffer[w_idx] * (int)X_buffer[x_idx];
              }
            }
          }
          int z_idx = ((oc * OW + ow) * OH + oh);
          Z_buffer[z_idx] = acc;
        }
      }
    }

    wait();
    compute.write(false);
    wait();
  }
}

void ACCNAME::Send() {
  testS.write(0);
  Send_si.write(0);
  wait();
  while (1) {
    Send_si.write(1);
    while (!send) wait();
    Send_si.write(2);
    DWAIT();

    const int Z_len = OC * OW * OH;
    for (int i = 0; i < Z_len; i++) {
      ADATA d;
      d.tlast = (i + 1 == Z_len);
      d.data = Z_buffer[i];
      dout1.write(d);
      testS.write(d.data);
      wait();
      DWAIT();
    }
    send.write(false);
    wait();
  }
}

