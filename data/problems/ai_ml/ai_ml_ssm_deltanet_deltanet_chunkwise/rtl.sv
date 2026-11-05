module ai_ml_ssm_deltanet_deltanet_chunkwise #(
    parameter int KEY_DIM   = 1,
    parameter int VALUE_DIM = 1,
    parameter int ELEM_W    = 16
) (
    input  logic clk,
    input  logic rst,

    input  logic in_valid,
    output logic in_ready,
    input  logic [KEY_DIM*ELEM_W-1:0]             in_q,
    input  logic [KEY_DIM*ELEM_W-1:0]             in_k,
    input  logic [VALUE_DIM*ELEM_W-1:0]           in_v,
    input  logic [ELEM_W-1:0]                     in_beta,
    input  logic [KEY_DIM*VALUE_DIM*ELEM_W-1:0]   in_state,

    output logic out_valid,
    input  logic out_ready,
    output logic [VALUE_DIM*ELEM_W-1:0]           out_o,
    output logic [KEY_DIM*VALUE_DIM*ELEM_W-1:0]   out_state
);

localparam int ACC_W = 3*ELEM_W + KEY_DIM + 4;

logic busy;
wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

genvar gj;
generate
    if (VALUE_DIM == 0) begin : gen_zero_out_o
        assign out_o = '0;
    end
endgenerate

genvar gs;
generate
    if (KEY_DIM == 0 || VALUE_DIM == 0) begin : gen_zero_out_state
        assign out_state = '0;
    end
endgenerate

always_ff @(posedge clk) begin
    integer i;
    integer j;
    integer flat;
    logic signed [ELEM_W-1:0] q_elem;
    logic signed [ELEM_W-1:0] k_elem;
    logic signed [ELEM_W-1:0] v_elem;
    logic signed [ELEM_W-1:0] beta_elem;
    logic signed [ELEM_W-1:0] state_elem;
    logic signed [ACC_W-1:0] proj;
    logic signed [ACC_W-1:0] residual;
    logic signed [ACC_W-1:0] next_state_elem;
    logic signed [ACC_W-1:0] out_acc;

    if (rst) begin
        busy      <= 1'b0;
        out_o     <= '0;
        out_state <= '0;
    end else if (input_accepted) begin
        beta_elem = $signed(in_beta);

        out_o     <= '0;
        out_state <= '0;

        for (j = 0; j < VALUE_DIM; j = j + 1) begin
            proj = '0;
            for (i = 0; i < KEY_DIM; i = i + 1) begin
                k_elem = $signed(in_k[ELEM_W*i +: ELEM_W]);
                flat = i * VALUE_DIM + j;
                state_elem = $signed(in_state[ELEM_W*flat +: ELEM_W]);
                proj = proj + k_elem * state_elem;
            end

            v_elem = $signed(in_v[ELEM_W*j +: ELEM_W]);
            residual = v_elem - proj;

            out_acc = '0;
            for (i = 0; i < KEY_DIM; i = i + 1) begin
                k_elem = $signed(in_k[ELEM_W*i +: ELEM_W]);
                q_elem = $signed(in_q[ELEM_W*i +: ELEM_W]);
                flat = i * VALUE_DIM + j;
                state_elem = $signed(in_state[ELEM_W*flat +: ELEM_W]);

                next_state_elem = state_elem + beta_elem * k_elem * residual;
                out_state[ELEM_W*flat +: ELEM_W] <= next_state_elem[ELEM_W-1:0];
                out_acc = out_acc + q_elem * next_state_elem;
            end

            out_o[ELEM_W*j +: ELEM_W] <= out_acc[ELEM_W-1:0];
        end

        busy <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule