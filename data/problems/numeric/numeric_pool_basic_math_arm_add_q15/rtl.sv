module q15_vector_add_4 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_a0,
    input  logic [15:0] in_a1,
    input  logic [15:0] in_a2,
    input  logic [15:0] in_a3,
    input  logic [15:0] in_b0,
    input  logic [15:0] in_b1,
    input  logic [15:0] in_b2,
    input  logic [15:0] in_b3,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_c0,
    output logic [15:0] out_c1,
    output logic [15:0] out_c2,
    output logic [15:0] out_c3
);

    logic busy;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    function automatic logic [15:0] sat_q15(input logic signed [16:0] value);
        begin
            sat_q15 = 16'h0000;
            if (value > 17'sd32767)
                sat_q15 = 16'h7FFF;
            else if (value < -17'sd32768)
                sat_q15 = 16'h8000;
            else
                sat_q15 = value[15:0];
        end
    endfunction

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy   <= 1'b0;
            out_c0 <= 16'h0000;
            out_c1 <= 16'h0000;
            out_c2 <= 16'h0000;
            out_c3 <= 16'h0000;
        end else if (input_accepted) begin
            out_c0 <= sat_q15($signed({in_a0[15], in_a0}) + $signed({in_b0[15], in_b0}));
            out_c1 <= sat_q15($signed({in_a1[15], in_a1}) + $signed({in_b1[15], in_b1}));
            out_c2 <= sat_q15($signed({in_a2[15], in_a2}) + $signed({in_b2[15], in_b2}));
            out_c3 <= sat_q15($signed({in_a3[15], in_a3}) + $signed({in_b3[15], in_b3}));
            busy   <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule