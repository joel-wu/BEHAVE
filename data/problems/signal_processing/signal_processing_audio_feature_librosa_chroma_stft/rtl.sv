module chroma_stft #(
    parameter int N_FREQ   = 5,
    parameter int N_CHROMA = 12,
    parameter int W_IN     = 32,
    parameter int W_COEF   = 32,
    parameter int W_OUT    = 32
) (
    input  logic                               clk,
    input  logic                               rst,

    input  logic                               in_valid,
    output logic                               in_ready,
    input  logic [N_FREQ*W_IN-1:0]             in_s,
    input  logic [N_CHROMA*N_FREQ*W_COEF-1:0]  in_chromafb,

    output logic                               out_valid,
    input  logic                               out_ready,
    output logic [N_CHROMA*W_OUT-1:0]          out_chroma
);

    localparam int PROD_W = W_IN + W_COEF;
    localparam int SUM_W  = PROD_W + $clog2(N_FREQ);
    localparam int RAW_W  = SUM_W;
    localparam int DIV_W  = RAW_W + 16;

    logic busy;
    logic [N_CHROMA*W_OUT-1:0] out_chroma_next;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin : compute_output
        logic [W_IN-1:0]   s_val;
        logic [W_COEF-1:0] coef_val;
        logic [PROD_W-1:0] prod;
        logic [SUM_W-1:0]  acc;
        logic [RAW_W-1:0]  raw_q16 [0:N_CHROMA-1];
        logic [RAW_W-1:0]  denom;
        logic [DIV_W-1:0]  num_ext;
        logic [DIV_W-1:0]  denom_ext;
        logic [DIV_W-1:0]  div_result;
        logic [W_OUT-1:0]  normed;
        int c;
        int f;

        out_chroma_next = '0;
        denom      = '0;
        num_ext    = '0;
        denom_ext  = '0;
        div_result = '0;
        normed     = '0;
        s_val      = '0;
        coef_val   = '0;
        prod       = '0;
        acc        = '0;

        for (c = 0; c < N_CHROMA; c = c + 1) begin
            acc = '0;
            for (f = 0; f < N_FREQ; f = f + 1) begin
                s_val    = in_s[f*W_IN +: W_IN];
                coef_val = in_chromafb[((c*N_FREQ + f)*W_COEF) +: W_COEF];
                prod     = s_val * coef_val;
                acc      = acc + {{(SUM_W-PROD_W){1'b0}}, prod};
            end
            raw_q16[c] = acc;
        end

        denom = raw_q16[0];
        for (c = 1; c < N_CHROMA; c = c + 1) begin
            if (raw_q16[c] > denom) begin
                denom = raw_q16[c];
            end
        end

        for (c = 0; c < N_CHROMA; c = c + 1) begin
            if (denom == '0) begin
                normed = '0;
            end else begin
                num_ext    = {raw_q16[c], 16'b0};
                denom_ext  = {{(DIV_W-RAW_W){1'b0}}, denom};
                div_result = num_ext / denom_ext;
                normed     = W_OUT'(div_result);
            end
            out_chroma_next[c*W_OUT +: W_OUT] = normed;
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy       <= 1'b0;
            out_chroma <= '0;
        end else if (input_accepted) begin
            out_chroma <= out_chroma_next;
            busy       <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
