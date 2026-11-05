// Brotli context-table data: Brotli Authors, MIT (see LICENSE.MIT).
module compression_brotli_brotli_context_lookup (
    input  logic       clk,
    input  logic       rst,

    input  logic       in_valid,
    output logic       in_ready,
    input  logic [1:0] in_mode,
    input  logic [7:0] in_p1,
    input  logic [7:0] in_p2,

    output logic       out_valid,
    input  logic       out_ready,
    output logic [7:0] out_context
);

localparam logic [1023:0] UTF8_LAST = 1024'h000c1c0c183c3c3c3c3c383c3c3c3c3c383c3c3c3c3c383c3c3c383c3c3c380c0c0c1c0c1834343434343034343434343034343434343034343430343434300c0c1c281820202c2c2c2c2c2c2c2c2c2c0c240c200c0c1c18100c140c0c100c080000000000000000000000000000000000000400000404000000000000000000;
localparam logic [1023:0] UTF8_PREVIOUS = 1024'h0001010101030303030303030303030303030303030303030303030303030301010101010102020202020202020202020202020202020202020202020202020101010101010102020202020202020202010101010101010101010101010101000000000000000000000000000000000000000000000000000000000000000000;
logic [7:0] last_value, previous_value, context_next;

function automatic logic [2:0] signed_class(input logic [7:0] value);
    if (value == 0) signed_class = 0;
    else if (value < 16) signed_class = 1;
    else if (value < 64) signed_class = 2;
    else if (value < 128) signed_class = 3;
    else if (value < 192) signed_class = 4;
    else if (value < 240) signed_class = 5;
    else if (value < 255) signed_class = 6;
    else signed_class = 7;
endfunction

assign in_ready = !out_valid || out_ready;
always_comb begin
    last_value = 0;
    previous_value = 0;
    if (in_p1 < 128) last_value = UTF8_LAST[8*in_p1 +: 8];
    else last_value = {6'd0, (in_p1 >= 192), in_p1[0]};
    if (in_p2 < 128) previous_value = UTF8_PREVIOUS[8*in_p2 +: 8];
    else if (in_p2 >= 224) previous_value = 2;
    case (in_mode)
        0: context_next = {2'b00, in_p1[5:0]};
        1: context_next = {2'b00, in_p1[7:2]};
        2: context_next = last_value | previous_value;
        default: context_next = {2'b00, signed_class(in_p1), signed_class(in_p2)};
    endcase
end

always_ff @(posedge clk) begin
    if (rst) begin
        out_valid <= 0;
        out_context <= 0;
    end else if (in_ready) begin
        out_valid <= in_valid;
        if (in_valid) out_context <= context_next;
    end
end
endmodule
