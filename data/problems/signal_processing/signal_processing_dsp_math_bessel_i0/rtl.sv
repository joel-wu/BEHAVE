module signal_processing_dsp_math_bessel_i0 (
    input  logic        clk,
    input  logic        rst,
    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_x,
    output logic        out_valid,
    input  logic        out_ready,
    output logic [63:0] out_y,
    output logic        out_error
);
    logic busy;
    logic [5:0] k;
    logic [24:0] square;
    logic [111:0] term, total;
    logic [127:0] product, quotient;
    logic [29:0] denominator;
    logic [111:0] next_total;

    assign in_ready = !rst && !busy && (!out_valid || out_ready);
    // A 128-bit product covers every valid request; no real arithmetic is used.
    always_comb begin
        product = {16'b0, term} * {{103{1'b0}}, square};
        denominator = 30'd262144 * {24'b0, k} * {24'b0, k};
        quotient = 0;
        if (denominator != 0)
            quotient = product / {{98{1'b0}}, denominator};
        next_total = total + quotient[111:0];
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy <= 0;
            out_valid <= 0;
            out_y <= 0;
            out_error <= 0;
            k <= 0;
            square <= 0;
            term <= 0;
            total <= 0;
        end else begin
            if (out_valid && out_ready)
                out_valid <= 0;
            if (in_valid && in_ready) begin
                if (in_x > 16'd4096) begin
                    out_y <= 0;
                    out_error <= 1;
                    out_valid <= 1;
                end else begin
                    square <= {9'b0, in_x} * {9'b0, in_x};
                    term <= 112'd1 << 80;
                    total <= 112'd1 << 80;
                    k <= 1;
                    busy <= 1;
                end
            end else if (busy) begin
                term <= quotient[111:0];
                total <= next_total;
                if (k == 32) begin
                    out_y <= next_total[111:48];
                    out_error <= 0;
                    out_valid <= 1;
                    busy <= 0;
                end else begin
                    k <= k + 1'b1;
                end
            end
        end
    end
endmodule
