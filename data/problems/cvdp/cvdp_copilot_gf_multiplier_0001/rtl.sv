module gf_multiplier (
    input logic [3:0] A, B,
    output logic [3:0] result
);
    logic [3:0] multiplicand;
    always_comb begin
        result=0;
        multiplicand=A;
        for (int i=0; i<4; i++) begin
            if (B[i]) result=result ^ multiplicand;
            multiplicand=(multiplicand << 1) ^ (multiplicand[3] ? 4'b0011 : 4'b0000);
        end
    end
endmodule
