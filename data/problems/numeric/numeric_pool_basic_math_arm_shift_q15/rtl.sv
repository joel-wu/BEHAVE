module q15_vector_shift4 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_x0,
    input  logic [15:0] in_x1,
    input  logic [15:0] in_x2,
    input  logic [15:0] in_x3,
    input  logic [7:0]  in_shiftBits,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_y0,
    output logic [15:0] out_y1,
    output logic [15:0] out_y2,
    output logic [15:0] out_y3
);

logic busy;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic logic [15:0] shift_lane(
    input logic [15:0] x_bits,
    input logic [7:0]  s_bits
);
    logic signed [15:0] x;
    logic signed [7:0]  s;
    logic signed [16:0] r17;
    logic signed [31:0] x32;
    logic signed [31:0] v32;
    begin
        shift_lane = 16'h0000;
        x = $signed(x_bits);
        s = $signed(s_bits);
        r17 = 17'sd0;
        x32 = 32'sd0;
        v32 = 32'sd0;

        if (s >= 0) begin
            if (x == 16'sd0) begin
                shift_lane = 16'h0000;
            end else if (s >= 8'sd15) begin
                if (x > 16'sd0) begin
                    shift_lane = 16'h7FFF;
                end else begin
                    shift_lane = 16'h8000;
                end
            end else begin
                x32 = {{16{x[15]}}, x};
                v32 = x32 <<< s[3:0];
                if (v32 > 32'sd32767) begin
                    shift_lane = 16'h7FFF;
                end else if (v32 < -32'sd32768) begin
                    shift_lane = 16'h8000;
                end else begin
                    shift_lane = v32[15:0];
                end
            end
        end else begin
            r17 = -s;
            if (r17 >= 17'sd16) begin
                if (x < 16'sd0) begin
                    shift_lane = 16'hFFFF;
                end else begin
                    shift_lane = 16'h0000;
                end
            end else begin
                shift_lane = $signed(x) >>> r17[3:0];
            end
        end
    end
endfunction

always_ff @(posedge clk) begin
    if (rst) begin
        busy   <= 1'b0;
        out_y0 <= 16'h0000;
        out_y1 <= 16'h0000;
        out_y2 <= 16'h0000;
        out_y3 <= 16'h0000;
    end else if (input_accepted) begin
        out_y0 <= shift_lane(in_x0, in_shiftBits);
        out_y1 <= shift_lane(in_x1, in_shiftBits);
        out_y2 <= shift_lane(in_x2, in_shiftBits);
        out_y3 <= shift_lane(in_x3, in_shiftBits);
        busy   <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule