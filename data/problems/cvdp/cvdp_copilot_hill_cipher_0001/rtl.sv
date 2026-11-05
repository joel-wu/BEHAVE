module hill_cipher (
    input logic clk, reset, start,
    input logic [14:0] plaintext,
    input logic [44:0] key,
    output logic [14:0] ciphertext,
    output logic done
);
    logic [1:0] phase;
    logic [14:0] plain_q, partial;
    logic [44:0] key_q;
    logic [11:0] row_sum;
    logic [4:0] residue;
    always_comb begin
        row_sum=0;
        if (phase!=0)
            for (int col=0; col<3; col++)
                row_sum=row_sum+12'(key_q[40-(3*(int'(phase)-1)+col)*5 +: 5])
                                   *12'(plain_q[10-col*5 +: 5]);
        residue=5'(row_sum % 12'd26);
    end
    always_ff @(posedge clk or posedge reset) begin
        if (reset) begin
            phase<=0; plain_q<=0; key_q<=0; partial<=0; ciphertext<=0; done<=0;
        end else begin
            done<=0;
            if (phase==0) begin
                if (start) begin phase<=1; plain_q<=plaintext; key_q<=key; partial<=0; end
            end else if (phase==3) begin
                ciphertext<={partial[14:5],residue}; done<=1; phase<=0;
            end else begin
                partial[15-int'(phase)*5 +: 5]<=residue;
                phase<=phase+1'b1;
            end
        end
    end
endmodule
