module cascaded_adder #(
    parameter int IN_DATA_WIDTH = 16,
    parameter int IN_DATA_NS = 4
) (
    input logic clk, input logic rst_n,
    input logic i_valid,
    input logic [IN_DATA_WIDTH*IN_DATA_NS-1:0] i_data,
    output logic o_valid,
    output logic [IN_DATA_WIDTH+$clog2(IN_DATA_NS)-1:0] o_data
);
    localparam int OUT_WIDTH = IN_DATA_WIDTH + $clog2(IN_DATA_NS);
    logic [IN_DATA_WIDTH*IN_DATA_NS-1:0] data_q;
    logic valid_q;
    logic [OUT_WIDTH-1:0] total;
    always_comb begin
        total = '0;
        for (int i=0; i<IN_DATA_NS; i++)
            total = total + OUT_WIDTH'(data_q[i*IN_DATA_WIDTH +: IN_DATA_WIDTH]);
    end
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            data_q <= '0; valid_q <= 0; o_data <= '0; o_valid <= 0;
        end else begin
            if (i_valid) data_q <= i_data;
            valid_q <= i_valid;
            o_data <= total;
            o_valid <= valid_q;
        end
    end
endmodule
