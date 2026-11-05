module pseudoRandGenerator_ca (
    input logic clock, init_reset, load_seed,
    input logic [15:0] CA_seed,
    input logic [1:0] rule_sel,
    output logic [15:0] CA_out
);
    logic [15:0] next_state;
    always_comb begin
        for (int i=0; i<16; i++) begin
            if (rule_sel==1)
                next_state[i]=(CA_out[i]^CA_out[(i+1)%16]) |
                    (~CA_out[(i+15)%16] & CA_out[i]);
            else
                next_state[i]=CA_out[(i+15)%16] ^ (CA_out[i] | CA_out[(i+1)%16]);
        end
    end
    always_ff @(posedge clock)
        if (init_reset) CA_out<=0;
        else if (load_seed) CA_out<=CA_seed;
        else CA_out<=next_state;
endmodule
