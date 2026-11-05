module dbi_enc (
    input logic clk,
    input logic rst_n,
    input logic [39:0] data_in,
    output logic [39:0] data_out,
    output logic [1:0] dbi_cntrl
);
    logic [39:0] next_data;
    logic [1:0] next_control;
    always_comb begin
        next_data = data_in;
        next_control = '0;
        for (int lane = 0; lane < 2; lane++) begin
            if ($countones(data_in[20*lane +: 20] ^ data_out[20*lane +: 20]) > 10) begin
                next_data[20*lane +: 20] = ~data_in[20*lane +: 20];
                next_control[lane] = 1;
            end
        end
    end
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            data_out <= '0;
            dbi_cntrl <= '0;
        end else begin
            data_out <= next_data;
            dbi_cntrl <= next_control;
        end
    end
endmodule
