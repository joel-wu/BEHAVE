module data_systems_gbdt_lgbm_numerical_decision_inner (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_fval,
    input  logic [31:0] in_threshold_in_bin,
    input  logic [7:0]  in_decision_type,
    input  logic [31:0] in_default_bin,
    input  logic [31:0] in_max_bin,
    input  logic [31:0] in_left_child,
    input  logic [31:0] in_right_child,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_next_node
);

logic busy;
logic [31:0] next_node_calc;
logic [1:0] missing_type;
logic       default_left;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    missing_type  = in_decision_type[3:2];
    default_left  = in_decision_type[1];
    next_node_calc = in_right_child;

    if (((missing_type == 2'd1) && (in_fval == in_default_bin)) ||
        ((missing_type == 2'd2) && (in_fval == in_max_bin))) begin
        if (default_left == 1'b1) begin
            next_node_calc = in_left_child;
        end else begin
            next_node_calc = in_right_child;
        end
    end else if (in_fval <= in_threshold_in_bin) begin
        next_node_calc = in_left_child;
    end else begin
        next_node_calc = in_right_child;
    end
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy          <= 1'b0;
        out_next_node <= 32'd0;
    end else if (input_accepted) begin
        busy          <= 1'b1;
        out_next_node <= next_node_calc;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule