module data_systems_reduce_argmax_reduce_16_int32 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_data_0,
    input  logic [31:0]  in_data_1,
    input  logic [31:0]  in_data_2,
    input  logic [31:0]  in_data_3,
    input  logic [31:0]  in_data_4,
    input  logic [31:0]  in_data_5,
    input  logic [31:0]  in_data_6,
    input  logic [31:0]  in_data_7,
    input  logic [31:0]  in_data_8,
    input  logic [31:0]  in_data_9,
    input  logic [31:0]  in_data_10,
    input  logic [31:0]  in_data_11,
    input  logic [31:0]  in_data_12,
    input  logic [31:0]  in_data_13,
    input  logic [31:0]  in_data_14,
    input  logic [31:0]  in_data_15,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_value,
    output logic [3:0]   out_index
);

logic busy;
wire  output_consumed;
wire  input_accepted;

logic signed [31:0] best_value_comb;
logic        [3:0]  best_index_comb;

logic signed [31:0] x0;
logic signed [31:0] x1;
logic signed [31:0] x2;
logic signed [31:0] x3;
logic signed [31:0] x4;
logic signed [31:0] x5;
logic signed [31:0] x6;
logic signed [31:0] x7;
logic signed [31:0] x8;
logic signed [31:0] x9;
logic signed [31:0] x10;
logic signed [31:0] x11;
logic signed [31:0] x12;
logic signed [31:0] x13;
logic signed [31:0] x14;
logic signed [31:0] x15;

assign x0  = $signed(in_data_0);
assign x1  = $signed(in_data_1);
assign x2  = $signed(in_data_2);
assign x3  = $signed(in_data_3);
assign x4  = $signed(in_data_4);
assign x5  = $signed(in_data_5);
assign x6  = $signed(in_data_6);
assign x7  = $signed(in_data_7);
assign x8  = $signed(in_data_8);
assign x9  = $signed(in_data_9);
assign x10 = $signed(in_data_10);
assign x11 = $signed(in_data_11);
assign x12 = $signed(in_data_12);
assign x13 = $signed(in_data_13);
assign x14 = $signed(in_data_14);
assign x15 = $signed(in_data_15);

assign out_valid = busy;
assign output_consumed = out_valid & out_ready;
assign in_ready = ~busy | output_consumed;
assign input_accepted = in_valid & in_ready;

always_comb begin
    best_value_comb = x0;
    best_index_comb = 4'd0;

    if (x1 > best_value_comb) begin
        best_value_comb = x1;
        best_index_comb = 4'd1;
    end
    if (x2 > best_value_comb) begin
        best_value_comb = x2;
        best_index_comb = 4'd2;
    end
    if (x3 > best_value_comb) begin
        best_value_comb = x3;
        best_index_comb = 4'd3;
    end
    if (x4 > best_value_comb) begin
        best_value_comb = x4;
        best_index_comb = 4'd4;
    end
    if (x5 > best_value_comb) begin
        best_value_comb = x5;
        best_index_comb = 4'd5;
    end
    if (x6 > best_value_comb) begin
        best_value_comb = x6;
        best_index_comb = 4'd6;
    end
    if (x7 > best_value_comb) begin
        best_value_comb = x7;
        best_index_comb = 4'd7;
    end
    if (x8 > best_value_comb) begin
        best_value_comb = x8;
        best_index_comb = 4'd8;
    end
    if (x9 > best_value_comb) begin
        best_value_comb = x9;
        best_index_comb = 4'd9;
    end
    if (x10 > best_value_comb) begin
        best_value_comb = x10;
        best_index_comb = 4'd10;
    end
    if (x11 > best_value_comb) begin
        best_value_comb = x11;
        best_index_comb = 4'd11;
    end
    if (x12 > best_value_comb) begin
        best_value_comb = x12;
        best_index_comb = 4'd12;
    end
    if (x13 > best_value_comb) begin
        best_value_comb = x13;
        best_index_comb = 4'd13;
    end
    if (x14 > best_value_comb) begin
        best_value_comb = x14;
        best_index_comb = 4'd14;
    end
    if (x15 > best_value_comb) begin
        best_value_comb = x15;
        best_index_comb = 4'd15;
    end
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy <= 1'b0;
        out_value <= 32'b0;
        out_index <= 4'b0;
    end else if (input_accepted) begin
        busy      <= 1'b1;
        out_value <= best_value_comb;
        out_index <= best_index_comb;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
