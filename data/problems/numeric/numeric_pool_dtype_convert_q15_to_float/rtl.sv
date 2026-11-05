module q15_to_float4 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_x0,
    input  logic [15:0] in_x1,
    input  logic [15:0] in_x2,
    input  logic [15:0] in_x3,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_y0,
    output logic [31:0] out_y1,
    output logic [31:0] out_y2,
    output logic [31:0] out_y3
);

logic busy;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic logic [31:0] q15_to_fp32(input logic [15:0] x);
    logic signed [15:0] s;
    logic [15:0] mag;
    logic [4:0] msb;
    logic [7:0] exponent;
    logic [22:0] frac;
    logic sign;
    logic [30:0] frac_tmp;
begin
    q15_to_fp32 = 32'h00000000;
    s = $signed(x);
    if (s == 16'sd0) begin
        q15_to_fp32 = 32'h00000000;
    end else begin
        sign = (s < 0);
        if (s < 0) begin
            mag = -s;
        end else begin
            mag = s[15:0];
        end

        msb = 5'd0;
        if (mag[15])       msb = 5'd15;
        else if (mag[14])  msb = 5'd14;
        else if (mag[13])  msb = 5'd13;
        else if (mag[12])  msb = 5'd12;
        else if (mag[11])  msb = 5'd11;
        else if (mag[10])  msb = 5'd10;
        else if (mag[9])   msb = 5'd9;
        else if (mag[8])   msb = 5'd8;
        else if (mag[7])   msb = 5'd7;
        else if (mag[6])   msb = 5'd6;
        else if (mag[5])   msb = 5'd5;
        else if (mag[4])   msb = 5'd4;
        else if (mag[3])   msb = 5'd3;
        else if (mag[2])   msb = 5'd2;
        else if (mag[1])   msb = 5'd1;
        else               msb = 5'd0;

        exponent = msb + 8'd112;
        frac_tmp = ({15'd0, mag} << (23 - msb)) & 31'h007FFFFF;
        frac = frac_tmp[22:0];

        q15_to_fp32 = {sign, exponent, frac};
    end
end
endfunction

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy   <= 1'b0;
        out_y0 <= 32'h00000000;
        out_y1 <= 32'h00000000;
        out_y2 <= 32'h00000000;
        out_y3 <= 32'h00000000;
    end else if (input_accepted) begin
        out_y0 <= q15_to_fp32(in_x0);
        out_y1 <= q15_to_fp32(in_x1);
        out_y2 <= q15_to_fp32(in_x2);
        out_y3 <= q15_to_fp32(in_x3);
        busy   <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule