module restoring_division #(parameter int WIDTH = 6) (
    input logic clk, rst, start,
    input logic [WIDTH-1:0] dividend, divisor,
    output logic [WIDTH-1:0] quotient, remainder,
    output logic valid
);
    localparam int CW = (WIDTH<2) ? 1 : $clog2(WIDTH+1);
    logic busy;
    logic [CW-1:0] left;
    logic [WIDTH-1:0] bits_q, divisor_q, q_work, r_work;
    logic [WIDTH:0] shifted, difference;
    logic fits;
    assign shifted = {r_work, bits_q[WIDTH-1]};
    assign fits = shifted >= {1'b0,divisor_q};
    assign difference = shifted - {1'b0,divisor_q};
    always_ff @(posedge clk or negedge rst) begin
        if (!rst) begin
            busy<=0; left<=0; bits_q<=0; divisor_q<=0; q_work<=0; r_work<=0;
            quotient<=0; remainder<=0; valid<=0;
        end else begin
            valid<=0;
            if (!busy) begin
                if (start) begin
                    busy<=1; left<=CW'(WIDTH); bits_q<=dividend; divisor_q<=divisor;
                    q_work<=0; r_work<=0;
                end
            end else begin
                bits_q<=bits_q<<1;
                q_work<=(q_work<<1) | WIDTH'(fits);
                r_work<=fits ? difference[WIDTH-1:0] : shifted[WIDTH-1:0];
                left<=left-1'b1;
                if (left==1) begin
                    busy<=0; valid<=1;
                    quotient<=(q_work<<1) | WIDTH'(fits);
                    remainder<=fits ? difference[WIDTH-1:0] : shifted[WIDTH-1:0];
                end
            end
        end
    end
endmodule
