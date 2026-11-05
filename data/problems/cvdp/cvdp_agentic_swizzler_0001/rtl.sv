module swizzler #(
    parameter int NUM_LANES = 4,
    parameter int DATA_WIDTH = 8,
    parameter int REGISTER_OUTPUT = 0,
    parameter int ENABLE_PARITY_CHECK = 0
) (
    input logic clk, rst_n, bypass,
    input logic [NUM_LANES*DATA_WIDTH-1:0] data_in,
    input logic [NUM_LANES*((NUM_LANES > 1) ? $clog2(NUM_LANES) : 1)-1:0] swizzle_map_flat,
    output wire [NUM_LANES*DATA_WIDTH-1:0] data_out,
    output wire parity_error
);
    localparam int INDEX_WIDTH = (NUM_LANES > 1) ? $clog2(NUM_LANES) : 1;
    wire [NUM_LANES*DATA_WIDTH-1:0] remapped;
    wire [NUM_LANES-1:0] odd;
    for (genvar i = 0; i < NUM_LANES; i++) begin : lanes
        wire [INDEX_WIDTH-1:0] index = swizzle_map_flat[i*INDEX_WIDTH +: INDEX_WIDTH];
        wire [DATA_WIDTH-1:0] value = bypass ? data_in[i*DATA_WIDTH +: DATA_WIDTH]
            : (index < NUM_LANES ? data_in[index*DATA_WIDTH +: DATA_WIDTH] : '0);
        assign remapped[i*DATA_WIDTH +: DATA_WIDTH] = value;
        assign odd[i] = ^value;
    end
    wire parity = ENABLE_PARITY_CHECK ? |odd : 1'b0;
    if (REGISTER_OUTPUT) begin : registered_output
        logic [NUM_LANES*DATA_WIDTH-1:0] saved_data;
        logic saved_parity;
        always_ff @(posedge clk or negedge rst_n) begin
            if (!rst_n) begin
                saved_data <= '0;
                saved_parity <= 1'b0;
            end else begin
                saved_data <= remapped;
                saved_parity <= parity;
            end
        end
        assign data_out = saved_data;
        assign parity_error = saved_parity;
    end else begin : combinational_output
        assign data_out = remapped;
        assign parity_error = parity;
    end
endmodule
