module data_systems_vector_search_pq_fast_scan_lut4_step (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [127:0] in_codes_packed,
    input  logic [127:0] in_lut,
    input  logic [511:0] in_accum,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [511:0] out_accum
);

logic busy;
logic [511:0] out_accum_next;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    logic [7:0]  byte_j;
    logic [15:0] acc_j;
    logic [3:0]  code_j;
    logic [7:0]  addend_j;
    logic [15:0] sum_j;
    integer j;
    integer lane;
    integer address;

    out_accum_next = 512'b0;
    for (j = 0; j < 32; j = j + 1) begin
        lane = j % 16;
        address = (lane < 8) ? 2*lane : 2*(lane-8)+1;
        byte_j = in_codes_packed[8*address +: 8];
        acc_j  = in_accum[16*j +: 16];
        if (j < 16)
            code_j = byte_j[3:0];
        else
            code_j = byte_j[7:4];
        addend_j = in_lut[8*code_j +: 8];
        sum_j = acc_j + {8'b0, addend_j};
        out_accum_next[16*j +: 16] = sum_j;
    end
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy      <= 1'b0;
        out_accum <= 512'h0;
    end else if (input_accepted) begin
        busy      <= 1'b1;
        out_accum <= out_accum_next;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
