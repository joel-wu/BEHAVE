module crossbar_3x2 (
    input logic clk, rst,
    input logic s0_valid, s0_dest,
    input logic [15:0] s0_data,
    output logic s0_ready,
    input logic s1_valid, s1_dest,
    input logic [15:0] s1_data,
    output logic s1_ready,
    input logic s2_valid, s2_dest,
    input logic [15:0] s2_data,
    output logic s2_ready,
    input logic m0_ready,
    output logic m0_valid,
    output logic [15:0] m0_data,
    output logic [1:0] m0_source,
    input logic m1_ready,
    output logic m1_valid,
    output logic [15:0] m1_data,
    output logic [1:0] m1_source
);
    logic [1:0] occupied;
    logic [15:0] data [0:1];
    logic [1:0] origin [0:1];
    logic [1:0] priority_ptr [0:1];
    wire [2:0] requests = {s2_valid, s1_valid, s0_valid};
    wire [2:0] destinations = {s2_dest, s1_dest, s0_dest};
    wire [1:0] ready = {m1_ready, m0_ready};
    wire [47:0] payloads = {s2_data, s1_data, s0_data};
    logic [2:0] source_ready;
    integer selected [0:1];
    integer m, delta, candidate;
    always @* begin
        source_ready = 0;
        candidate = 0;
        for (m=0; m<2; m=m+1) begin
            selected[m] = -1;
            if (!rst && (!occupied[m] || ready[m])) begin
                for (delta=0; delta<3; delta=delta+1) begin
                    candidate = priority_ptr[m] + delta;
                    if (candidate >= 3) candidate = candidate - 3;
                    if (selected[m] == -1 && requests[candidate] && destinations[candidate] == m) begin
                        selected[m] = candidate;
                        source_ready[candidate] = 1;
                    end
                end
            end
        end
    end
    assign {s2_ready, s1_ready, s0_ready} = source_ready;
    assign m0_valid = occupied[0];
    assign m1_valid = occupied[1];
    assign m0_data = occupied[0] ? data[0] : 16'd0;
    assign m1_data = occupied[1] ? data[1] : 16'd0;
    assign m0_source = occupied[0] ? origin[0] : 2'd0;
    assign m1_source = occupied[1] ? origin[1] : 2'd0;
    integer j;
    always @(posedge clk) begin
        if (rst) begin
            occupied <= 0;
            for (j=0; j<2; j=j+1) begin
                data[j] <= 0;
                origin[j] <= 0;
                priority_ptr[j] <= 0;
            end
        end else begin
            for (j=0; j<2; j=j+1) begin
                if (ready[j]) occupied[j] <= 0;
                if (selected[j] >= 0) begin
                    occupied[j] <= 1;
                    data[j] <= payloads[16*selected[j] +: 16];
                    origin[j] <= selected[j];
                    priority_ptr[j] <= selected[j] == 2 ? 0 : selected[j]+1;
                end
            end
        end
    end
endmodule
