module priority_encoder_8x3 (
    input logic [7:0] value,
    output logic [2:0] out
);
    always_comb begin
        out=0;
        for (int bit_index=0; bit_index<8; bit_index++)
            if (value[bit_index]) out=3'(bit_index);
    end
endmodule
