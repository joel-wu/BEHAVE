module ai_ml_arch_systolic_pe_basic_mac (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [7:0]   in_a,
    input  logic [15:0]  in_b,
    input  logic [15:0]  in_d,
    input  logic         in_control_dataflow,
    input  logic         in_control_propagate,
    input  logic [4:0]   in_control_shift,
    input  logic [3:0]   in_id,
    input  logic         in_last,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [7:0]   out_a,
    output logic [15:0]  out_b,
    output logic [15:0]  out_c,
    output logic         out_control_dataflow,
    output logic         out_control_propagate,
    output logic [4:0]   out_control_shift,
    output logic [3:0]   out_id,
    output logic         out_last,
    output logic         bad_dataflow
);

    logic [15:0] c1;
    logic [15:0] c2;
    logic        last_propagate;

    logic [15:0] calc_out_b;
    logic [15:0] calc_out_c;
    logic [15:0] next_c1;
    logic [15:0] next_c2;
    logic        flip;
    logic [4:0]  shift_offset;
    logic [23:0] mul_full;
    logic [23:0] add_full;

    wire input_accepted  = in_valid & in_ready;
    wire output_consumed = out_valid & out_ready;

    assign in_ready = !rst && (!out_valid || out_ready);

    always_comb begin
        flip         = (last_propagate != in_control_propagate);
        shift_offset = flip ? in_control_shift : 5'd0;

        calc_out_b = 16'h0000;
        calc_out_c = 16'h0000;
        next_c1    = c1;
        next_c2    = c2;
        mul_full   = 24'h000000;
        add_full   = 24'h000000;

        if (in_control_dataflow == 1'b0) begin
            if (in_control_propagate == 1'b1) begin
                if (shift_offset >= 5'd16) begin
                    calc_out_c = 16'h0000;
                end else begin
                    calc_out_c = c1 >> shift_offset;
                end
                calc_out_b = in_b;
                mul_full   = in_a * in_b;
                add_full   = {8'h00, c2} + mul_full;
                next_c1    = in_d;
                next_c2    = add_full[15:0];
            end else begin
                if (shift_offset >= 5'd16) begin
                    calc_out_c = 16'h0000;
                end else begin
                    calc_out_c = c2 >> shift_offset;
                end
                calc_out_b = in_b;
                mul_full   = in_a * in_b;
                add_full   = {8'h00, c1} + mul_full;
                next_c1    = add_full[15:0];
                next_c2    = in_d;
            end
        end else begin
            if (in_control_propagate == 1'b1) begin
                calc_out_c = c1;
                mul_full   = in_a * c2;
                add_full   = {8'h00, in_b} + mul_full;
                calc_out_b = add_full[15:0];
                next_c1    = in_d;
                next_c2    = c2;
            end else begin
                calc_out_c = c2;
                mul_full   = in_a * c1;
                add_full   = {8'h00, in_b} + mul_full;
                calc_out_b = add_full[15:0];
                next_c1    = c1;
                next_c2    = in_d;
            end
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            c1                    <= 16'h0000;
            c2                    <= 16'h0000;
            last_propagate        <= 1'b0;
            out_valid             <= 1'b0;
            out_a                 <= 8'h00;
            out_b                 <= 16'h0000;
            out_c                 <= 16'h0000;
            out_control_dataflow  <= 1'b0;
            out_control_propagate <= 1'b0;
            out_control_shift     <= 5'h00;
            out_id                <= 4'h0;
            out_last              <= 1'b0;
            bad_dataflow          <= 1'b0;
        end else begin
            if (input_accepted) begin
                out_a                 <= in_a;
                out_b                 <= calc_out_b;
                out_c                 <= calc_out_c;
                out_control_dataflow  <= in_control_dataflow;
                out_control_propagate <= in_control_propagate;
                out_control_shift     <= in_control_shift;
                out_id                <= in_id;
                out_last              <= in_last;
                bad_dataflow          <= 1'b0;
                out_valid             <= 1'b1;

                c1             <= next_c1;
                c2             <= next_c2;
                last_propagate <= in_control_propagate;
            end else if (output_consumed) begin
                out_valid <= 1'b0;
            end
        end
    end

endmodule
