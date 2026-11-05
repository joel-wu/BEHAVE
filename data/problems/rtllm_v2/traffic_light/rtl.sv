module traffic_light (
    input logic clk,
    input logic rst_n,
    input logic pass_request,
    output logic [7:0] clock,
    output logic red,
    output logic yellow,
    output logic green
);
    localparam logic [1:0] IDLE=2'd0, RED=2'd1, YELLOW=2'd2, GREEN=2'd3;
    logic [1:0] phase;
    logic p_red, p_yellow, p_green;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            phase <= IDLE;
            clock <= 8'd10;
            p_red <= 1'b0;
            p_yellow <= 1'b0;
            p_green <= 1'b0;
            red <= 1'b0;
            yellow <= 1'b0;
            green <= 1'b0;
        end else begin
            red <= p_red;
            yellow <= p_yellow;
            green <= p_green;
            if (pass_request && green && clock > 8'd10) clock <= 8'd10;
            else if (!green && p_green) clock <= 8'd60;
            else if (!yellow && p_yellow) clock <= 8'd5;
            else if (!red && p_red) clock <= 8'd10;
            else clock <= clock - 8'd1;
            case (phase)
                IDLE: begin
                    {p_red,p_yellow,p_green} <= 3'b000;
                    phase <= RED;
                end
                RED: begin
                    {p_red,p_yellow,p_green} <= 3'b100;
                    if (clock == 8'd3) phase <= GREEN;
                end
                YELLOW: begin
                    {p_red,p_yellow,p_green} <= 3'b010;
                    if (clock == 8'd3) phase <= RED;
                end
                GREEN: begin
                    {p_red,p_yellow,p_green} <= 3'b001;
                    if (clock == 8'd3) phase <= YELLOW;
                end
            endcase
        end
    end
endmodule
