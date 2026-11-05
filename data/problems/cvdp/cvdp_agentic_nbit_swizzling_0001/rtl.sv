module nbit_swizzling #(parameter int DATA_WIDTH = 64) (
    input logic [DATA_WIDTH-1:0] data_in,
    input logic [1:0] sel,
    output logic [DATA_WIDTH-1:0] data_out, gray_out
);
    for (genvar i=0;i<DATA_WIDTH;i++) begin: permute
        localparam int H=DATA_WIDTH/2, Q=DATA_WIDTH/4, E=DATA_WIDTH/8;
        always_comb case(sel)
            0: data_out[i]=data_in[DATA_WIDTH-1-i];
            1: data_out[i]=data_in[(i/H)*H+H-1-(i%H)];
            2: data_out[i]=data_in[(i/Q)*Q+Q-1-(i%Q)];
            3: data_out[i]=data_in[(i/E)*E+E-1-(i%E)];
        endcase
    end
    assign gray_out=data_out^(data_out>>1);
endmodule
