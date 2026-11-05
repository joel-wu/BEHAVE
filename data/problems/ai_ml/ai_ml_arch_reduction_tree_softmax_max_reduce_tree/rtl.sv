module reduction_tree_softmax_max_reduce_tree (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_x0,
    input  logic [7:0]  in_x1,
    input  logic [7:0]  in_x2,
    input  logic [7:0]  in_x3,
    input  logic [7:0]  in_x4,
    input  logic [7:0]  in_x5,
    input  logic [7:0]  in_x6,
    input  logic [7:0]  in_x7,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [7:0]  out_max
);

logic busy;

wire input_accepted;
wire output_consumed;

logic [7:0] s0;
logic [7:0] s1;
logic [7:0] s2;
logic [7:0] s3;
logic [7:0] t0;
logic [7:0] t1;
logic [7:0] max_result;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    s0 = (in_x0 > in_x1) ? in_x0 : in_x1;
    s1 = (in_x2 > in_x3) ? in_x2 : in_x3;
    s2 = (in_x4 > in_x5) ? in_x4 : in_x5;
    s3 = (in_x6 > in_x7) ? in_x6 : in_x7;
    t0 = (s0    > s1   ) ? s0    : s1;
    t1 = (s2    > s3   ) ? s2    : s3;
    max_result = (t0    > t1   ) ? t0    : t1;
end

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy    <= 1'b0;
        out_max <= 8'h00;
    end else if (input_accepted) begin
        busy    <= 1'b1;
        out_max <= max_result;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule