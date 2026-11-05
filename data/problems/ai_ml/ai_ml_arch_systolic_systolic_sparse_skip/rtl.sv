module systolic_sparse_skip_pe (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_a,
    input  logic [15:0] in_b,
    input  logic [15:0] in_d,

    input  logic        in_control_dataflow,
    input  logic        in_control_propagate,
    input  logic [4:0]  in_control_shift,

    input  logic [7:0]  in_id,
    input  logic        in_last,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [7:0]  out_a,
    output logic [15:0] out_b,
    output logic [15:0] out_c,

    output logic        out_control_dataflow,
    output logic        out_control_propagate,
    output logic [4:0]  out_control_shift,

    output logic [7:0]  out_id,
    output logic        out_last,

    output logic        bad_dataflow
);

    logic [15:0] c1;
    logic [15:0] c2;
    logic        last_propagate;

    logic        busy;
    wire         input_accepted;
    wire         output_consumed;

    logic signed [7:0]  a_s;
    logic signed [15:0] b_s;
    logic signed [15:0] old_c1_s;
    logic signed [15:0] old_c2_s;
    logic [4:0]         shift_offset;

    logic signed [31:0] mac32;
    logic [15:0]        next_out_b;
    logic [15:0]        next_out_c;

    function automatic logic [15:0] ashr16(input logic [15:0] v, input logic [4:0] s);
        logic signed [15:0] x;
        begin
            x = v;
            if (s >= 5'd16) begin
                if (x < 0)
                    ashr16 = 16'hFFFF;
                else
                    ashr16 = 16'h0000;
            end else begin
                ashr16 = x >>> s;
            end
        end
    endfunction

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        a_s      = in_a;
        b_s      = in_b;
        old_c1_s = c1;
        old_c2_s = c2;

        if (last_propagate != in_control_propagate)
            shift_offset = in_control_shift;
        else
            shift_offset = 5'd0;

        next_out_b = 16'h0000;
        next_out_c = 16'h0000;
        mac32      = 32'sd0;

        if (in_control_dataflow == 1'b0) begin
            next_out_b = in_b;
            if (in_control_propagate == 1'b1) begin
                next_out_c = ashr16(c1, shift_offset);
                mac32      = old_c2_s + (a_s * b_s);
            end else begin
                next_out_c = ashr16(c2, shift_offset);
                mac32      = old_c1_s + (a_s * b_s);
            end
        end else begin
            if (in_control_propagate == 1'b1) begin
                next_out_c = c1;
                mac32      = b_s + (a_s * old_c2_s);
                next_out_b = mac32[15:0];
            end else begin
                next_out_c = c2;
                mac32      = b_s + (a_s * old_c1_s);
                next_out_b = mac32[15:0];
            end
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            c1                    <= 16'h0000;
            c2                    <= 16'h0000;
            last_propagate        <= 1'b0;
            busy                  <= 1'b0;

            out_a                 <= 8'h00;
            out_b                 <= 16'h0000;
            out_c                 <= 16'h0000;
            out_control_dataflow  <= 1'b0;
            out_control_propagate <= 1'b0;
            out_control_shift     <= 5'h00;
            out_id                <= 8'h00;
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

            if (in_control_dataflow == 1'b0) begin
                if (in_control_propagate == 1'b1) begin
                    c2 <= mac32[15:0];
                    c1 <= in_d;
                end else begin
                    c1 <= mac32[15:0];
                    c2 <= in_d;
                end
            end else begin
                if (in_control_propagate == 1'b1) begin
                    c1 <= in_d;
                    c2 <= c2;
                end else begin
                    c1 <= c1;
                    c2 <= in_d;
                end
            end

            last_propagate <= in_control_propagate;
            busy           <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule