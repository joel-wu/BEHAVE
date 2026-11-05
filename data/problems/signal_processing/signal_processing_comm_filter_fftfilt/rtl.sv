module signal_processing_comm_filter_fftfilt #(
    parameter logic [127:0] COEFFS = {32'sd1, 32'sd3, -32'sd2, 32'sd1}
) (
    input  logic               clk,
    input  logic               rst,

    input  logic               in_valid,
    output logic               in_ready,
    input  logic [31:0]        in_x0,
    input  logic [31:0]        in_x1,
    input  logic [31:0]        in_x2,
    input  logic [31:0]        in_x3,
    input  logic [31:0]        in_x4,
    input  logic [31:0]        in_x5,
    input  logic [31:0]        in_x6,
    input  logic [31:0]        in_x7,

    output logic               out_valid,
    input  logic               out_ready,
    output logic [31:0]        out_y0,
    output logic [31:0]        out_y1,
    output logic [31:0]        out_y2,
    output logic [31:0]        out_y3,
    output logic [31:0]        out_y4,
    output logic [31:0]        out_y5,
    output logic [31:0]        out_y6,
    output logic [31:0]        out_y7
);

    logic busy;

    logic signed [31:0] delay_line [0:3];

    logic signed [31:0] xs [0:7];

    integer i;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    assign xs[0] = $signed(in_x0);
    assign xs[1] = $signed(in_x1);
    assign xs[2] = $signed(in_x2);
    assign xs[3] = $signed(in_x3);
    assign xs[4] = $signed(in_x4);
    assign xs[5] = $signed(in_x5);
    assign xs[6] = $signed(in_x6);
    assign xs[7] = $signed(in_x7);

    always_ff @(posedge clk) begin
        logic signed [31:0] d0, d1, d2, d3;
        logic signed [65:0] acc;
        logic signed [31:0] y_arr [0:7];

        if (rst) begin
            busy <= 1'b0;
            for (i = 0; i < 4; i = i + 1) begin
                delay_line[i] <= 32'sd0;
            end
            out_y0 <= 32'd0;
            out_y1 <= 32'd0;
            out_y2 <= 32'd0;
            out_y3 <= 32'd0;
            out_y4 <= 32'd0;
            out_y5 <= 32'd0;
            out_y6 <= 32'd0;
            out_y7 <= 32'd0;
        end else if (input_accepted) begin
            // Walk delay line one sample at a time
            d0 = delay_line[0];
            d1 = delay_line[1];
            d2 = delay_line[2];
            d3 = delay_line[3];

            for (i = 0; i < 8; i = i + 1) begin
                d3 = d2;
                d2 = d1;
                d1 = d0;
                d0 = xs[i];
                acc = $signed(COEFFS[ 0 +: 32]) * d0
                    + $signed(COEFFS[32 +: 32]) * d1
                    + $signed(COEFFS[64 +: 32]) * d2
                    + $signed(COEFFS[96 +: 32]) * d3;
                y_arr[i] = acc[31:0];
            end

            out_y0 <= y_arr[0];
            out_y1 <= y_arr[1];
            out_y2 <= y_arr[2];
            out_y3 <= y_arr[3];
            out_y4 <= y_arr[4];
            out_y5 <= y_arr[5];
            out_y6 <= y_arr[6];
            out_y7 <= y_arr[7];

            delay_line[0] <= d0;
            delay_line[1] <= d1;
            delay_line[2] <= d2;
            delay_line[3] <= d3;

            busy <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
