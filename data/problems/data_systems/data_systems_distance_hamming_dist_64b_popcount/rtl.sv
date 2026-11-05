module data_systems_distance_hamming_dist_64b_popcount (
    input  logic        clk,
    input  logic        rst,
    input  logic        in_valid,
    output logic        in_ready,
    input  logic [63:0] in_bs1,
    input  logic [63:0] in_bs2,
    output logic        out_valid,
    input  logic        out_ready,
    output logic [6:0]  out_distance
);

logic busy;
logic [63:0] diff;
logic [6:0] popcount_result;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    diff = in_bs1 ^ in_bs2;
    popcount_result =
        7'(diff[0])  + 7'(diff[1])  + 7'(diff[2])  + 7'(diff[3]) +
        7'(diff[4])  + 7'(diff[5])  + 7'(diff[6])  + 7'(diff[7]) +
        7'(diff[8])  + 7'(diff[9])  + 7'(diff[10]) + 7'(diff[11]) +
        7'(diff[12]) + 7'(diff[13]) + 7'(diff[14]) + 7'(diff[15]) +
        7'(diff[16]) + 7'(diff[17]) + 7'(diff[18]) + 7'(diff[19]) +
        7'(diff[20]) + 7'(diff[21]) + 7'(diff[22]) + 7'(diff[23]) +
        7'(diff[24]) + 7'(diff[25]) + 7'(diff[26]) + 7'(diff[27]) +
        7'(diff[28]) + 7'(diff[29]) + 7'(diff[30]) + 7'(diff[31]) +
        7'(diff[32]) + 7'(diff[33]) + 7'(diff[34]) + 7'(diff[35]) +
        7'(diff[36]) + 7'(diff[37]) + 7'(diff[38]) + 7'(diff[39]) +
        7'(diff[40]) + 7'(diff[41]) + 7'(diff[42]) + 7'(diff[43]) +
        7'(diff[44]) + 7'(diff[45]) + 7'(diff[46]) + 7'(diff[47]) +
        7'(diff[48]) + 7'(diff[49]) + 7'(diff[50]) + 7'(diff[51]) +
        7'(diff[52]) + 7'(diff[53]) + 7'(diff[54]) + 7'(diff[55]) +
        7'(diff[56]) + 7'(diff[57]) + 7'(diff[58]) + 7'(diff[59]) +
        7'(diff[60]) + 7'(diff[61]) + 7'(diff[62]) + 7'(diff[63]);
end

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy         <= 1'b0;
        out_distance <= 7'd0;
    end else if (input_accepted) begin
        busy         <= 1'b1;
        out_distance <= popcount_result;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule