module cic_decimator #(
    parameter int WIDTH = 16,
    parameter int RMAX = 2,
    parameter int M = 1,
    parameter int N = 2,
    parameter int REG_WIDTH = WIDTH + $clog2((RMAX * M)**N)
) (
    input logic clk, rst,
    input logic [WIDTH-1:0] input_tdata,
    input logic input_tvalid,
    output logic input_tready,
    output logic [REG_WIDTH-1:0] output_tdata,
    output logic output_tvalid,
    input logic output_tready,
    input logic [((RMAX < 2) ? 1 : $clog2(RMAX+1))-1:0] rate
);
    localparam int RATE_WIDTH = (RMAX < 2) ? 1 : $clog2(RMAX+1);
    logic [REG_WIDTH-1:0] integrator [0:N-1];
    logic [REG_WIDTH-1:0] delay_value [0:N*M-1];
    wire [REG_WIDTH-1:0] comb_value [0:N];
    logic [RATE_WIDTH-1:0] cycle_reg;
    wire [RATE_WIDTH-1:0] selected_rate = rate == 0 ? RATE_WIDTH'(1) :
        (rate > RATE_WIDTH'(RMAX) ? RATE_WIDTH'(RMAX) : rate);
    wire accept = input_tvalid && input_tready;
    wire emit_output = output_tvalid && output_tready;
    assign input_tready = !rst && (output_tready || cycle_reg != 0);
    assign output_tvalid = !rst && input_tvalid && cycle_reg == 0;
    assign comb_value[0] = integrator[N-1];
    for (genvar stage=0; stage<N; stage=stage+1) begin: comb_chain
        assign comb_value[stage+1] = comb_value[stage]-delay_value[stage*M+M-1];
    end
    assign output_tdata = comb_value[N];
    always_ff @(posedge clk) begin
        if (rst) begin
            cycle_reg <= 0;
            for (int stage=0; stage<N; stage++) integrator[stage] <= 0;
            for (int cell_index=0; cell_index<N*M; cell_index++) delay_value[cell_index] <= 0;
        end else begin
            if (accept) begin
                integrator[0] <= integrator[0]+REG_WIDTH'($signed(input_tdata));
                for (int stage=1; stage<N; stage++)
                    integrator[stage] <= integrator[stage]+integrator[stage-1];
                cycle_reg <= cycle_reg >= selected_rate-1'b1 ? '0 : cycle_reg+1'b1;
            end
            if (emit_output) begin
                for (int stage=0; stage<N; stage++) begin
                    delay_value[stage*M] <= comb_value[stage];
                    for (int lag=1; lag<M; lag++)
                        delay_value[stage*M+lag] <= delay_value[stage*M+lag-1];
                end
            end
        end
    end
endmodule
