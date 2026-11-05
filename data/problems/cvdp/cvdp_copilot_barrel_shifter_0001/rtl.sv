module barrel_shifter_8bit (
    input logic [7:0] data_in,
    input logic [2:0] shift_bits,
    input logic left_right,
    output logic [7:0] data_out
);
    assign data_out=left_right ? (data_in << shift_bits) : (data_in >> shift_bits);
endmodule
