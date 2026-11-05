module lfsr_way_selector #(
    parameter logic [15:0] SEED  = 16'h0000,
    parameter int unsigned WIDTH = 16
) (
    input  logic                     clk,
    input  logic                     rst,

    input  logic                     in_valid,
    output logic                     in_ready,
    input  logic                     in_en,

    output logic                     out_valid,
    input  logic                     out_ready,
    output logic [WIDTH-1:0]         out_refill_way_oh,
    output logic [$clog2(WIDTH)-1:0] out_refill_way_bin
);

    localparam int unsigned LOGWIDTH = $clog2(WIDTH);

    logic                   busy;
    logic [15:0]            shift_q;
    logic [15:0]            shift_q_next;
    logic [LOGWIDTH-1:0]    refill_way_bin_next;
    logic [WIDTH-1:0]       refill_way_oh_next;

    wire input_accepted  = in_valid & in_ready;
    wire output_consumed = out_valid & out_ready;
    wire shift_in        = ~((shift_q[15] ^ shift_q[12]) ^ (shift_q[5] ^ shift_q[1]));

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    assign shift_q_next         = in_en ? {shift_q[14:0], shift_in} : shift_q;
    assign refill_way_bin_next  = shift_q_next[LOGWIDTH-1:0];
    assign refill_way_oh_next   = ({WIDTH{1'b0}} | {{(WIDTH-1){1'b0}}, 1'b1}) << refill_way_bin_next;

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            shift_q            <= SEED;
            out_refill_way_bin <= SEED[LOGWIDTH-1:0];
            out_refill_way_oh  <= ({WIDTH{1'b0}} | {{(WIDTH-1){1'b0}}, 1'b1}) << SEED[LOGWIDTH-1:0];
            busy               <= 1'b0;
        end else if (input_accepted) begin
            shift_q            <= shift_q_next;
            out_refill_way_bin <= refill_way_bin_next;
            out_refill_way_oh  <= refill_way_oh_next;
            busy               <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule