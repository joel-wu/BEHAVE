module hamming_code_tx_for_4bit (
    input logic [3:0] data_in,
    output logic [7:0] data_out
);
    assign data_out={data_in[3:1],data_in[1]^data_in[2]^data_in[3],
        data_in[0],data_in[0]^data_in[2]^data_in[3],
        data_in[0]^data_in[1]^data_in[3],1'b0};
endmodule
