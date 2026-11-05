module unpack_one_hot (
    input logic sign, size,
    input logic [2:0] one_hot_selector,
    input logic [255:0] source_reg,
    output logic [511:0] destination_reg
);
    always_comb begin
        destination_reg='0;
        case (one_hot_selector)
            1: for (int i=0; i<64; i++)
                destination_reg[8*i +: 8]={{7{sign & source_reg[i]}}, source_reg[i]};
            2: for (int i=0; i<64; i++)
                destination_reg[8*i +: 8]={{6{sign & source_reg[2*i+1]}}, source_reg[2*i +: 2]};
            4: if (size) begin
                for (int i=0; i<32; i++)
                    destination_reg[16*i +: 16]={{8{sign & source_reg[8*i+7]}}, source_reg[8*i +: 8]};
            end else begin
                for (int i=0; i<64; i++)
                    destination_reg[8*i +: 8]={{4{sign & source_reg[4*i+3]}}, source_reg[4*i +: 4]};
            end
            default: destination_reg={256'b0,source_reg};
        endcase
    end
endmodule
