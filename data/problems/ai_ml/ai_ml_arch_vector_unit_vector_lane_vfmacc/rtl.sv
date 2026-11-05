module ai_ml_arch_vector_unit_vector_lane_vfmacc (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic signed [15:0] in_a,
    input  logic signed [31:0] in_b,
    input  logic signed [31:0] in_d,

    input  logic         in_control_dataflow,
    input  logic         in_control_propagate,
    input  logic [4:0]   in_control_shift,

    input  logic [7:0]   in_id,
    input  logic         in_last,

    output logic         out_valid,
    input  logic         out_ready,
    output logic signed [15:0] out_a,
    output logic signed [31:0] out_b,
    output logic signed [31:0] out_c,

    output logic         out_control_dataflow,
    output logic         out_control_propagate,
    output logic [4:0]   out_control_shift,

    output logic [7:0]   out_id,
    output logic         out_last,
    output logic         bad_dataflow
);

    logic busy;

    logic signed [31:0] c1;
    logic signed [31:0] c2;
    logic               last_propagate;

    logic signed [47:0] prod48;
    logic signed [47:0] sum48;
    logic               flip;
    logic [4:0]         shift_offset;
    logic signed [31:0] shifted_c1;
    logic signed [31:0] shifted_c2;
    logic signed [31:0] next_out_b;
    logic signed [31:0] next_out_c;
    logic signed [31:0] next_c1;
    logic signed [31:0] next_c2;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    assign flip = (last_propagate != in_control_propagate);
    assign shift_offset = flip ? in_control_shift : 5'd0;

    assign shifted_c1 = c1 >>> shift_offset;
    assign shifted_c2 = c2 >>> shift_offset;

    always_comb begin
        prod48  = $signed(in_a) * $signed(in_b);
        sum48   = 48'sd0;
        next_out_b = 32'sd0;
        next_out_c = 32'sd0;
        next_c1    = c1;
        next_c2    = c2;

        if (in_control_dataflow == 1'b0) begin
            if (in_control_propagate == 1'b1) begin
                next_out_c = shifted_c1;
                next_out_b = in_b;
                sum48      = $signed(c2) + prod48;
                next_c2    = sum48[31:0];
                next_c1    = in_d;
            end else begin
                next_out_c = shifted_c2;
                next_out_b = in_b;
                sum48      = $signed(c1) + prod48;
                next_c1    = sum48[31:0];
                next_c2    = in_d;
            end
        end else begin
            if (in_control_propagate == 1'b1) begin
                next_out_c = c1;
                sum48      = $signed(in_b) + ($signed(in_a) * $signed(c2));
                next_out_b = sum48[31:0];
                next_c1    = in_d;
                next_c2    = c2;
            end else begin
                next_out_c = c2;
                sum48      = $signed(in_b) + ($signed(in_a) * $signed(c1));
                next_out_b = sum48[31:0];
                next_c2    = in_d;
                next_c1    = c1;
            end
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy                  <= 1'b0;
            c1                    <= 32'sd0;
            c2                    <= 32'sd0;
            last_propagate        <= 1'b0;
            out_a                 <= 16'sd0;
            out_b                 <= 32'sd0;
            out_c                 <= 32'sd0;
            out_control_dataflow  <= 1'b0;
            out_control_propagate <= 1'b0;
            out_control_shift     <= 5'd0;
            out_id                <= 8'd0;
            out_last              <= 1'b0;
            bad_dataflow          <= 1'b0;
        end else if (input_accepted) begin
            out_a                 <= in_a;
            out_b                 <= next_out_b;
            out_c                 <= next_out_c;
            out_control_dataflow  <= in_control_dataflow;
            out_control_propagate <= in_control_propagate;
            out_control_shift     <= in_control_shift;
            out_id                <= in_id;
            out_last              <= in_last;
            bad_dataflow          <= 1'b0;

            c1                    <= next_c1;
            c2                    <= next_c2;
            last_propagate        <= in_control_propagate;
            busy                  <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule