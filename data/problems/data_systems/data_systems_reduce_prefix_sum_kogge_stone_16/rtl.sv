module data_systems_reduce_prefix_sum_kogge_stone_16 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_x0,
    input  logic [31:0]  in_x1,
    input  logic [31:0]  in_x2,
    input  logic [31:0]  in_x3,
    input  logic [31:0]  in_x4,
    input  logic [31:0]  in_x5,
    input  logic [31:0]  in_x6,
    input  logic [31:0]  in_x7,
    input  logic [31:0]  in_x8,
    input  logic [31:0]  in_x9,
    input  logic [31:0]  in_x10,
    input  logic [31:0]  in_x11,
    input  logic [31:0]  in_x12,
    input  logic [31:0]  in_x13,
    input  logic [31:0]  in_x14,
    input  logic [31:0]  in_x15,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_y0,
    output logic [31:0]  out_y1,
    output logic [31:0]  out_y2,
    output logic [31:0]  out_y3,
    output logic [31:0]  out_y4,
    output logic [31:0]  out_y5,
    output logic [31:0]  out_y6,
    output logic [31:0]  out_y7,
    output logic [31:0]  out_y8,
    output logic [31:0]  out_y9,
    output logic [31:0]  out_y10,
    output logic [31:0]  out_y11,
    output logic [31:0]  out_y12,
    output logic [31:0]  out_y13,
    output logic [31:0]  out_y14,
    output logic [31:0]  out_y15
);

logic busy;

logic [31:0] s1_0;
logic [31:0] s1_1;
logic [31:0] s1_2;
logic [31:0] s1_3;
logic [31:0] s1_4;
logic [31:0] s1_5;
logic [31:0] s1_6;
logic [31:0] s1_7;
logic [31:0] s1_8;
logic [31:0] s1_9;
logic [31:0] s1_10;
logic [31:0] s1_11;
logic [31:0] s1_12;
logic [31:0] s1_13;
logic [31:0] s1_14;
logic [31:0] s1_15;

logic [31:0] s2_0;
logic [31:0] s2_1;
logic [31:0] s2_2;
logic [31:0] s2_3;
logic [31:0] s2_4;
logic [31:0] s2_5;
logic [31:0] s2_6;
logic [31:0] s2_7;
logic [31:0] s2_8;
logic [31:0] s2_9;
logic [31:0] s2_10;
logic [31:0] s2_11;
logic [31:0] s2_12;
logic [31:0] s2_13;
logic [31:0] s2_14;
logic [31:0] s2_15;

logic [31:0] s3_0;
logic [31:0] s3_1;
logic [31:0] s3_2;
logic [31:0] s3_3;
logic [31:0] s3_4;
logic [31:0] s3_5;
logic [31:0] s3_6;
logic [31:0] s3_7;
logic [31:0] s3_8;
logic [31:0] s3_9;
logic [31:0] s3_10;
logic [31:0] s3_11;
logic [31:0] s3_12;
logic [31:0] s3_13;
logic [31:0] s3_14;
logic [31:0] s3_15;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy    <= 1'b0;
        out_y0  <= 32'h00000000;
        out_y1  <= 32'h00000000;
        out_y2  <= 32'h00000000;
        out_y3  <= 32'h00000000;
        out_y4  <= 32'h00000000;
        out_y5  <= 32'h00000000;
        out_y6  <= 32'h00000000;
        out_y7  <= 32'h00000000;
        out_y8  <= 32'h00000000;
        out_y9  <= 32'h00000000;
        out_y10 <= 32'h00000000;
        out_y11 <= 32'h00000000;
        out_y12 <= 32'h00000000;
        out_y13 <= 32'h00000000;
        out_y14 <= 32'h00000000;
        out_y15 <= 32'h00000000;
    end else if (input_accepted) begin
        s1_0  = in_x0;
        s1_1  = in_x1  + in_x0;
        s1_2  = in_x2  + in_x1;
        s1_3  = in_x3  + in_x2;
        s1_4  = in_x4  + in_x3;
        s1_5  = in_x5  + in_x4;
        s1_6  = in_x6  + in_x5;
        s1_7  = in_x7  + in_x6;
        s1_8  = in_x8  + in_x7;
        s1_9  = in_x9  + in_x8;
        s1_10 = in_x10 + in_x9;
        s1_11 = in_x11 + in_x10;
        s1_12 = in_x12 + in_x11;
        s1_13 = in_x13 + in_x12;
        s1_14 = in_x14 + in_x13;
        s1_15 = in_x15 + in_x14;

        s2_0  = s1_0;
        s2_1  = s1_1;
        s2_2  = s1_2  + s1_0;
        s2_3  = s1_3  + s1_1;
        s2_4  = s1_4  + s1_2;
        s2_5  = s1_5  + s1_3;
        s2_6  = s1_6  + s1_4;
        s2_7  = s1_7  + s1_5;
        s2_8  = s1_8  + s1_6;
        s2_9  = s1_9  + s1_7;
        s2_10 = s1_10 + s1_8;
        s2_11 = s1_11 + s1_9;
        s2_12 = s1_12 + s1_10;
        s2_13 = s1_13 + s1_11;
        s2_14 = s1_14 + s1_12;
        s2_15 = s1_15 + s1_13;

        s3_0  = s2_0;
        s3_1  = s2_1;
        s3_2  = s2_2;
        s3_3  = s2_3;
        s3_4  = s2_4  + s2_0;
        s3_5  = s2_5  + s2_1;
        s3_6  = s2_6  + s2_2;
        s3_7  = s2_7  + s2_3;
        s3_8  = s2_8  + s2_4;
        s3_9  = s2_9  + s2_5;
        s3_10 = s2_10 + s2_6;
        s3_11 = s2_11 + s2_7;
        s3_12 = s2_12 + s2_8;
        s3_13 = s2_13 + s2_9;
        s3_14 = s2_14 + s2_10;
        s3_15 = s2_15 + s2_11;

        out_y0  <= s3_0;
        out_y1  <= s3_1;
        out_y2  <= s3_2;
        out_y3  <= s3_3;
        out_y4  <= s3_4;
        out_y5  <= s3_5;
        out_y6  <= s3_6;
        out_y7  <= s3_7;
        out_y8  <= s3_8  + s3_0;
        out_y9  <= s3_9  + s3_1;
        out_y10 <= s3_10 + s3_2;
        out_y11 <= s3_11 + s3_3;
        out_y12 <= s3_12 + s3_4;
        out_y13 <= s3_13 + s3_5;
        out_y14 <= s3_14 + s3_6;
        out_y15 <= s3_15 + s3_7;

        busy <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule