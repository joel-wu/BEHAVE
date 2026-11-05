module hamming_code_receiver (
    input logic [7:0] data_in,
    output logic [3:0] data_out
);
    wire [2:0] syndrome={data_in[4]^data_in[5]^data_in[6]^data_in[7],
        data_in[2]^data_in[3]^data_in[6]^data_in[7],
        data_in[1]^data_in[3]^data_in[5]^data_in[7]};
    wire [7:0] corrected=data_in ^ (syndrome==0 ? 8'b0 : (8'b1 << syndrome));
    assign data_out={corrected[7:5],corrected[3]};
endmodule
