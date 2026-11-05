module cvdp_prbs_gen #(
    parameter int CHECK_MODE = 0, parameter int POLY_LENGTH = 31,
    parameter int POLY_TAP = 3, parameter int WIDTH = 16
) (
    input logic clk, rst,
    input logic [WIDTH-1:0] data_in,
    output logic [WIDTH-1:0] data_out
);
    logic [POLY_LENGTH-1:0] stages, next_stages;
    logic [WIDTH-1:0] next_word;
    logic feedback;
    always_comb begin
        next_stages=stages;
        next_word='0;
        feedback=0;
        for (int i=0; i<WIDTH; i++) begin
            feedback=next_stages[POLY_LENGTH-POLY_TAP] ^ next_stages[0];
            next_word[i]=feedback ^ (CHECK_MODE ? data_in[i] : 1'b0);
            next_stages=next_stages >> 1;
            next_stages[POLY_LENGTH-1]=CHECK_MODE ? data_in[i] : feedback;
        end
    end
    always_ff @(posedge clk) begin
        if (rst) begin stages<='1; data_out<='1; end
        else begin stages<=next_stages; data_out<=next_word; end
    end
endmodule
