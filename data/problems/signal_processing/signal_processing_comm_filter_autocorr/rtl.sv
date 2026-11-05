module signal_processing_comm_filter_autocorr (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_x_re,
    input  logic [31:0] in_x_im,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [67:0] out_rxx_re,
    output logic [67:0] out_rxx_im,
    output logic [66:0] out_energy
);

    logic busy;
    wire  input_accepted;
    wire  output_consumed;

    logic signed [31:0] w_re [0:7];
    logic signed [31:0] w_im [0:7];

    logic signed [31:0] wdelay_re [0:11];
    logic signed [32:0] wdelay_im [0:11];

    logic [63:0] we2 [0:7];
    logic [2:0]  ie2;
    logic [66:0] e2_sum;

    logic signed [32:0] x_im_conj_ext;
    logic signed [64:0] e2_re_term;
    logic signed [64:0] e2_im_term;
    logic [63:0]        e2_val;
    logic [63:0]        old_e2_val;
    logic [66:0]        e2_sum_next;

    logic signed [67:0] rxx_re_calc;
    logic signed [67:0] rxx_im_calc;

    logic signed [31:0] w_new_re [0:7];
    logic signed [31:0] w_new_im [0:7];
    logic signed [31:0] wdelay_new_re [0:11];
    logic signed [32:0] wdelay_new_im [0:11];

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        x_im_conj_ext = -$signed({in_x_im[31], in_x_im});

        w_new_re[0] = $signed(in_x_re);
        w_new_im[0] = $signed(in_x_im);
        for (int i = 1; i < 8; i = i + 1) begin
            w_new_re[i] = w_re[i-1];
            w_new_im[i] = w_im[i-1];
        end

        wdelay_new_re[0] = $signed(in_x_re);
        wdelay_new_im[0] = x_im_conj_ext;
        for (int i = 1; i < 12; i = i + 1) begin
            wdelay_new_re[i] = wdelay_re[i-1];
            wdelay_new_im[i] = wdelay_im[i-1];
        end

        rxx_re_calc = 68'sd0;
        rxx_im_calc = 68'sd0;
        for (int i = 0; i < 8; i = i + 1) begin
            logic signed [64:0] ac;
            logic signed [65:0] bd;
            logic signed [65:0] ad;
            logic signed [64:0] bc;
            logic signed [67:0] term_re;
            logic signed [67:0] term_im;

            ac = $signed(w_new_re[i]) * $signed(wdelay_new_re[i+4]);
            bd = $signed(w_new_im[i]) * $signed(wdelay_new_im[i+4]);
            ad = $signed(w_new_re[i]) * $signed(wdelay_new_im[i+4]);
            bc = $signed(w_new_im[i]) * $signed(wdelay_new_re[i+4]);

            term_re = $signed(ac) - $signed(bd);
            term_im = $signed(ad) + $signed(bc);

            rxx_re_calc = rxx_re_calc + term_re;
            rxx_im_calc = rxx_im_calc + term_im;
        end

        e2_re_term = $signed(in_x_re) * $signed(in_x_re);
        e2_im_term = $signed(in_x_im) * $signed(in_x_im);
        e2_val     = $unsigned(e2_re_term[63:0]) + $unsigned(e2_im_term[63:0]);

        unique case (ie2)
            3'd0: old_e2_val = we2[0];
            3'd1: old_e2_val = we2[1];
            3'd2: old_e2_val = we2[2];
            3'd3: old_e2_val = we2[3];
            3'd4: old_e2_val = we2[4];
            3'd5: old_e2_val = we2[5];
            3'd6: old_e2_val = we2[6];
            3'd7: old_e2_val = we2[7];
        endcase

        e2_sum_next = e2_sum - {{3{1'b0}}, old_e2_val} + {{3{1'b0}}, e2_val};
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy       <= 1'b0;
            out_rxx_re <= 68'd0;
            out_rxx_im <= 68'd0;
            out_energy <= 67'd0;
            ie2        <= 3'd0;
            e2_sum     <= 67'd0;

            for (int i = 0; i < 8; i = i + 1) begin
                w_re[i] <= 32'sd0;
                w_im[i] <= 32'sd0;
                we2[i]  <= 64'd0;
            end
            for (int i = 0; i < 12; i = i + 1) begin
                wdelay_re[i] <= 32'sd0;
                wdelay_im[i] <= 33'sd0;
            end
        end else begin
            if (input_accepted) begin
                out_rxx_re <= rxx_re_calc;
                out_rxx_im <= rxx_im_calc;
                out_energy <= e2_sum_next;
                busy       <= 1'b1;

                for (int i = 0; i < 8; i = i + 1) begin
                    w_re[i] <= w_new_re[i];
                    w_im[i] <= w_new_im[i];
                end
                for (int i = 0; i < 12; i = i + 1) begin
                    wdelay_re[i] <= wdelay_new_re[i];
                    wdelay_im[i] <= wdelay_new_im[i];
                end

                unique case (ie2)
                    3'd0: we2[0] <= e2_val;
                    3'd1: we2[1] <= e2_val;
                    3'd2: we2[2] <= e2_val;
                    3'd3: we2[3] <= e2_val;
                    3'd4: we2[4] <= e2_val;
                    3'd5: we2[5] <= e2_val;
                    3'd6: we2[6] <= e2_val;
                    3'd7: we2[7] <= e2_val;
                endcase

                e2_sum <= e2_sum_next;
                if (ie2 == 3'd7) begin
                    ie2 <= 3'd0;
                end else begin
                    ie2 <= ie2 + 3'd1;
                end
            end else if (output_consumed) begin
                busy <= 1'b0;
            end
        end
    end

endmodule
