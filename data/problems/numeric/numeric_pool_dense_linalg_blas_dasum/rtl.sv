module vector_absolute_sum6 (
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

    output logic         out_valid,
    input  logic         out_ready,
    output logic [34:0]  out_sum
);

logic busy;

wire input_accepted;
wire output_consumed;

logic [32:0] abs_0;
logic [32:0] abs_1;
logic [32:0] abs_2;
logic [32:0] abs_3;
logic [32:0] abs_4;
logic [32:0] abs_5;

logic [34:0] sum0;
logic [34:0] sum1;
logic [34:0] sum2;
logic [34:0] sum3;
logic [34:0] sum4;
logic [34:0] sum5;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    if (in_x0[31]) abs_0 = {1'b0, (~in_x0 + 32'd1)};
    else           abs_0 = {1'b0, in_x0};

    if (in_x1[31]) abs_1 = {1'b0, (~in_x1 + 32'd1)};
    else           abs_1 = {1'b0, in_x1};

    if (in_x2[31]) abs_2 = {1'b0, (~in_x2 + 32'd1)};
    else           abs_2 = {1'b0, in_x2};

    if (in_x3[31]) abs_3 = {1'b0, (~in_x3 + 32'd1)};
    else           abs_3 = {1'b0, in_x3};

    if (in_x4[31]) abs_4 = {1'b0, (~in_x4 + 32'd1)};
    else           abs_4 = {1'b0, in_x4};

    if (in_x5[31]) abs_5 = {1'b0, (~in_x5 + 32'd1)};
    else           abs_5 = {1'b0, in_x5};

    sum0 = {2'b00, abs_0};
    sum1 = sum0 + {2'b00, abs_1};
    sum2 = sum1 + {2'b00, abs_2};
    sum3 = sum2 + {2'b00, abs_3};
    sum4 = sum3 + {2'b00, abs_4};
    sum5 = sum4 + {2'b00, abs_5};
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy    <= 1'b0;
        out_sum <= 35'd0;
    end else if (input_accepted) begin
        busy    <= 1'b1;
        out_sum <= sum5;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule