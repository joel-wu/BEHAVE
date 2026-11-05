module ai_ml_arch_systolic_systolic_int8_quant (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic signed [7:0]  in_a,
    input  logic signed [31:0] in_b,
    input  logic signed [31:0] in_d,

    input  logic        in_control_dataflow,
    input  logic        in_control_propagate,
    input  logic [4:0]  in_control_shift,

    input  logic [0:0]  in_id,
    input  logic        in_last,

    output logic        out_valid,
    input  logic        out_ready,
    output logic signed [7:0]  out_a,
    output logic signed [31:0] out_b,
    output logic signed [31:0] out_c,

    output logic        out_control_dataflow,
    output logic        out_control_propagate,
    output logic [4:0]  out_control_shift,

    output logic [0:0]  out_id,
    output logic        out_last,

    output logic        bad_dataflow
);

    logic signed [31:0] c1;
    logic signed [31:0] c2;
    logic               last_propagate;

    logic               busy;
    wire                output_consumed;
    wire                input_accepted;

    logic signed [15:0] product16;
    logic signed [31:0] product32;
    logic [4:0]         shift_offset;

    logic signed [7:0]  b_low8_s;
    logic signed [7:0]  weight8;

    logic signed [31:0] next_out_b;
    logic signed [31:0] next_out_c;
    logic signed [31:0] next_c1;
    logic signed [31:0] next_c2;

    assign output_consumed = out_valid & out_ready;
    assign in_ready        = ~busy | output_consumed;
    assign input_accepted  = in_valid & in_ready;
    assign out_valid       = busy;
    assign bad_dataflow    = 1'b0;

    always_comb begin
        b_low8_s    = in_b[7:0];
        weight8     = 8'sd0;
        product16   = 16'sd0;
        product32   = 32'sd0;
        shift_offset = 5'd0;
        next_out_b  = in_b;
        next_out_c  = 32'sd0;
        next_c1     = c1;
        next_c2     = c2;

        if (last_propagate != in_control_propagate) begin
            shift_offset = in_control_shift;
        end

        if (in_control_dataflow == 1'b0) begin
            product16 = in_a * b_low8_s;
            product32 = product16;

            next_out_b = in_b;
            if (in_control_propagate == 1'b1) begin
                next_out_c = c1 >>> shift_offset;
                next_c1    = in_d;
                next_c2    = c2 + product32;
            end else begin
                next_out_c = c2 >>> shift_offset;
                next_c1    = c1 + product32;
                next_c2    = in_d;
            end
        end else begin
            if (in_control_propagate == 1'b1) begin
                weight8    = c2[7:0];
                product16  = in_a * weight8;
                product32  = product16;
                next_out_c = c1;
                next_out_b = in_b + product32;
                next_c1    = in_d;
                next_c2    = c2;
            end else begin
                weight8    = c1[7:0];
                product16  = in_a * weight8;
                product32  = product16;
                next_out_c = c2;
                next_out_b = in_b + product32;
                next_c1    = c1;
                next_c2    = in_d;
            end
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            c1                    <= 32'sd0;
            c2                    <= 32'sd0;
            last_propagate        <= 1'b0;
            busy                  <= 1'b0;

            out_a                 <= 8'sd0;
            out_b                 <= 32'sd0;
            out_c                 <= 32'sd0;
            out_control_dataflow  <= 1'b0;
            out_control_propagate <= 1'b0;
            out_control_shift     <= 5'd0;
            out_id                <= 1'b0;
            out_last              <= 1'b0;
        end else if (input_accepted) begin
            c1             <= next_c1;
            c2             <= next_c2;
            last_propagate <= in_control_propagate;
            busy           <= 1'b1;

            out_a                 <= in_a;
            out_b                 <= next_out_b;
            out_c                 <= next_out_c;
            out_control_dataflow  <= in_control_dataflow;
            out_control_propagate <= in_control_propagate;
            out_control_shift     <= in_control_shift;
            out_id                <= in_id;
            out_last              <= in_last;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule