module vector_exp4_f32 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_x0,
    input  logic [31:0] in_x1,
    input  logic [31:0] in_x2,
    input  logic [31:0] in_x3,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_y0,
    output logic [31:0] out_y1,
    output logic [31:0] out_y2,
    output logic [31:0] out_y3
);

// Nearest-even (2^48 / j!), j=0..12. The largest value is exactly 2^48.
function automatic logic [63:0] coefficient(input integer j);
    case (j)
        0, 1: coefficient = 64'd281474976710656;
        2: coefficient = 64'd140737488355328;
        3: coefficient = 64'd46912496118443;
        4: coefficient = 64'd11728124029611;
        5: coefficient = 64'd2345624805922;
        6: coefficient = 64'd390937467654;
        7: coefficient = 64'd55848209665;
        8: coefficient = 64'd6981026208;
        9: coefficient = 64'd775669579;
        10: coefficient = 64'd77566958;
        11: coefficient = 64'd7051542;
        12: coefficient = 64'd587628;
        default: coefficient = 64'd0;
    endcase
endfunction
localparam logic signed [63:0] LN2_Q48 = 64'sd195103586505167;

function automatic logic [63:0] round_shift(input logic [63:0] value,
                                            input integer shift);
    logic [63:0] quotient, remainder, halfway;
    begin
        quotient = value >> shift;
        remainder = value - (quotient << shift);
        halfway = 64'd1 << (shift - 1);
        round_shift = quotient + 64'((remainder > halfway) ||
                                    ((remainder == halfway) && quotient[0]));
    end
endfunction

function automatic logic [31:0] lane_exp(input logic [31:0] word);
    logic sign;
    integer exponent, n, shift;
    logic [63:0] magnitude, remainder, polynomial, significand;
    logic signed [63:0] x, quotient;
    logic [127:0] product;
    begin
        sign = word[31];
        exponent = int'(word[30:23]);
        if (exponent == 255) begin
            lane_exp = (word[22:0] != 0) ? 32'h7fc00000 :
                       (sign ? 32'h00000000 : 32'h7f800000);
        end else if (exponent <= 100) begin
            lane_exp = 32'h3f800000;
        end else if (exponent >= 134) begin
            lane_exp = sign ? 32'h00000000 : 32'h7f800000;
        end else begin
            magnitude = {40'd0, 1'b1, word[22:0]};
            if (exponent >= 102) magnitude = magnitude << (exponent - 102);
            else magnitude = magnitude >> (102 - exponent);
            x = sign ? -$signed(magnitude) : $signed(magnitude);
            if (x >= (64'sd89 <<< 48)) begin
                lane_exp = 32'h7f800000;
            end else if (x <= -(64'sd104 <<< 48)) begin
                lane_exp = 32'h00000000;
            end else begin
                // Signed division truncates; correct to floor for x < 0.
                quotient = x / LN2_Q48;
                if (x < 0 && (x % LN2_Q48) != 0) quotient = quotient - 1;
                n = int'(quotient);
                remainder = $unsigned(x - quotient * LN2_Q48);
                polynomial = coefficient(12);
                for (integer j = 11; j >= 0; j = j - 1) begin
                    product = 128'(remainder) * 128'(polynomial);
                    polynomial = coefficient(j) + 64'(product >> 48);
                end
                if (n < -126) begin
                    shift = -n - 101;
                    lane_exp = 32'(round_shift(polynomial, shift));
                end else begin
                    significand = round_shift(polynomial, 25);
                    if (significand == (64'd1 << 24)) begin
                        significand = significand >> 1;
                        n = n + 1;
                    end
                    lane_exp = n > 127 ? 32'h7f800000 :
                               (32'(n + 127) << 23) | 32'(significand - (64'd1 << 23));
                end
            end
        end
    end
endfunction

logic busy;
wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_ff @(posedge clk) begin
    if (rst) begin
        busy   <= 1'b0;
        out_y0 <= 32'h00000000;
        out_y1 <= 32'h00000000;
        out_y2 <= 32'h00000000;
        out_y3 <= 32'h00000000;
    end else if (input_accepted) begin
        out_y0 <= lane_exp(in_x0);
        out_y1 <= lane_exp(in_x1);
        out_y2 <= lane_exp(in_x2);
        out_y3 <= lane_exp(in_x3);
        busy   <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
