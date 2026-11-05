module nbit_swizzling #(
    parameter int DATA_WIDTH = 64
) (
    input logic [DATA_WIDTH-1:0] data_in,
    input logic [1:0] sel,
    output logic [DATA_WIDTH-1:0] data_out
);
    wire [DATA_WIDTH-1:0] reversed [0:3];
    for (genvar mode=0; mode<4; mode++) begin: modes
        localparam int SECTION_WIDTH=DATA_WIDTH/(1<<mode);
        for (genvar bit_index=0; bit_index<DATA_WIDTH; bit_index++) begin: wiring
            assign reversed[mode][bit_index]=data_in[(bit_index/SECTION_WIDTH)*SECTION_WIDTH+
                                                    SECTION_WIDTH-1-(bit_index%SECTION_WIDTH)];
        end
    end
    assign data_out=reversed[sel];
endmodule
