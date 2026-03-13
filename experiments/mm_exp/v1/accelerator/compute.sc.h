
void ACCNAME::Recv() {

  done.write(0);
  bool started = start.read();
  compute.write(false);
  send.write(false);
  Recv_si.write(0);
  wait();
  while (1) {
    Recv_si.write(1);
    wait();

    opcode packet(din1.read().data);
    code_extension op_args(din1.read().data, din1.read().data,
                           din1.read().data);
    acc_args = op_args;

    if (packet.read_A) {
      unsigned int read_length = op_args.N * op_args.K;
      for (int i = 0; i < read_length; i++) {
        A_buffer[i] = din1.read().data;
        DWAIT();
      }
    }

    if (packet.read_B) {
      unsigned int read_length = op_args.M * op_args.K;
      for (int i = 0; i < read_length; i++) {
        B_buffer[i] = din1.read().data;
        DWAIT();
      }
    }

    // Computes C if true
    if (packet.compute_C) {
      compute.write(true);
      wait();
    }

    Recv_si.write(2);

    while (compute) wait();

    // Sends then clears C if true
    if (packet.send_C) {
      send.write(true);
      wait();
    }

    Recv_si.write(3);
    while (send) wait();

    wait();
  }
}

void ACCNAME::compute_tile(int N, int M, int K, int in_stride, int out_stride) {
  for (int n = 0; n < N_Unroll; n++) {
#pragma HLS LOOP_TRIPCOUNT min = N_Unroll max = N_Unroll
#pragma HLS UNROLL factor = N_Unroll
    for (int m = 0; m < M_Unroll; m++) {
#pragma HLS LOOP_TRIPCOUNT min = M_Unroll max = M_Unroll
#pragma HLS UNROLL factor = M_Unroll
      int acc = 0;
      for (int k = 0; k < K_Unroll; k++) {
#pragma HLS PIPELINE II = 1
#pragma HLS LOOP_TRIPCOUNT min = K_Unroll max = K_Unroll
#pragma HLS UNROLL factor = K_Unroll
        int a_data = A_buffer[(N + n) * in_stride + K + k];
        int b_data = B_buffer[(M + m) * in_stride + K + k];
        acc += a_data * b_data;
      }
      C_buffer[(N + n) * out_stride + M + m] += acc;
    }
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

    for (int n = 0; n < acc_args.N; n += N_Unroll) {
      for (int m = 0; m < acc_args.M; m += M_Unroll) {
        for (int k = 0; k < acc_args.K; k += K_Unroll) {
          compute_tile(n, m, k, acc_args.K, acc_args.M);
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

    for (int n = 0; n < acc_args.N; n++) {
      for (int m = 0; m < acc_args.M; m++) {
        ADATA d;
        d.tlast = false;
        d.data = C_buffer[n * acc_args.M + m];
        if (n + 1 == acc_args.N && m + 1 == acc_args.M) d.tlast = true;
        dout1.write(d);
        testS.write(d.data);
        wait();
        C_buffer[n * acc_args.M + m] = 0;
        DWAIT();
      }
    }
    send.write(false);
    wait();
  }
}