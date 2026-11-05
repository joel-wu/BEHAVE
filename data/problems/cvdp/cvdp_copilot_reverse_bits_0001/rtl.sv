module reverse_bits (
    input logic [31:0] num_in,
    output logic [31:0] num_out
);
    for (genvar i=0; i<32; i++) begin: wiring
        assign num_out[i]=num_in[31-i];
    end
endmodule
