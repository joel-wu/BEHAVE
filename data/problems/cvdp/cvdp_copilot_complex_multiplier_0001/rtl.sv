module complex_multiplier (
    input logic clk,
    input logic arst_n,
    input logic [15:0] a_real,
    input logic [15:0] a_imag,
    input logic [15:0] b_real,
    input logic [15:0] b_imag,
    output logic [31:0] result_real,
    output logic [31:0] result_imag
);
    wire signed [15:0] a = $signed(a_real), b = $signed(a_imag);
    wire signed [15:0] c = $signed(b_real), d = $signed(b_imag);
    wire signed [31:0] ac = a*c, bd = b*d, ad = a*d, bc = b*c;
    always_ff @(posedge clk or negedge arst_n) begin
        if (!arst_n) begin
            result_real <= '0;
            result_imag <= '0;
        end else begin
            result_real <= ac - bd;
            result_imag <= ad + bc;
        end
    end
endmodule
